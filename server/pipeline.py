#!/usr/bin/env python3
# 服务端快路径管道（全 DashScope）：链接 → 直链 → ffmpeg → Fun-ASR + qwen-vl + qwen 摘要 → markdown
# 中间步骤都是确定性提取或本地 ffmpeg；AI 调用走 DashScope（服务端无本地模型）。
import base64
import datetime
import difflib
import glob
import json
import os
import re
import subprocess
import tempfile
import urllib.request

import dashscope
from dashscope import Generation, MultiModalConversation
from dashscope.audio.asr import Recognition

dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]

SPH_API = os.environ["SPH_API"].rstrip("/")
PARSE_PATH = os.environ.get("PARSE_PATH", "/api/fetch_video_profile")
VLM_MODEL = os.environ.get("DASHSCOPE_MODEL_VL", "qwen-vl-max")
ASR_MODEL = os.environ.get("DASHSCOPE_MODEL_ASR", "paraformer-realtime-v2")
TEXT_MODEL = os.environ.get("DASHSCOPE_MODEL_TEXT", "qwen-max")
SAMPLE_FPS = os.environ.get("SAMPLE_FPS", "1")
MPDECIMATE = os.environ.get("MPDECIMATE", "hi=64*48:lo=64*16:frac=0.02")
MAX_FRAMES = int(os.environ.get("VISUAL_MAX_FRAMES", "60"))
VISUAL_MODE = os.environ.get("VISUAL_MODE", "both")  # both|audio|visual
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

VL_PROMPT = ("这是视频中的一帧，通常是幻灯片或信息卡。用中文提取画面里的关键信息："
             "标题、要点文字、图表或数据的含义。若是纯装饰/转场或无实质信息，只回复(无新信息)。"
             "只输出提取到的内容，不要客套话。")


def log(*a):
    print("[pipeline]", *a, flush=True)


def resolve_link(url):
    # 有 SPH_COOKIE → 本地直连 yuanbao+channels 解析（国内服务端推荐，绕开 Cloudflare）
    cookie = os.environ.get("SPH_COOKIE", "")
    if cookie:
        import parse_sph
        return parse_sph.resolve(url, cookie)
    # 回退：Cloudflare Worker
    body = json.dumps({"url": url}).encode()
    req = urllib.request.Request(SPH_API + PARSE_PATH, data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    fi = (d.get("data") or {}).get("feedInfo") or {}
    src = ((fi.get("h264VideoInfo") or {}).get("videoUrl")
           or (fi.get("h265VideoInfo") or {}).get("videoUrl")
           or fi.get("videoUrl") or "")
    return src, fi.get("description", "")


def download_video(src, workdir):
    # http 直链先用 curl -4 下到本地（绕开 ffmpeg 在部分机器上的 IPv6/协议问题，且只下一次）
    if not src.startswith("http"):
        return src
    out = os.path.join(workdir, "v.mp4")
    subprocess.run(["curl", "-sS", "-4", "-A", UA, "-o", out, "--max-time", "180", src], check=False)
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


def extract_audio(local, workdir):
    wav = os.path.join(workdir, "a.wav")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", local,
                    "-vn", "-ar", "16000", "-ac", "1", wav], check=False)
    return wav if os.path.exists(wav) and os.path.getsize(wav) > 0 else None


def extract_frames(local, workdir):
    d = os.path.join(workdir, "frames")
    os.makedirs(d, exist_ok=True)
    vf = f"fps={SAMPLE_FPS},mpdecimate={MPDECIMATE},scale='min(1280,iw)':-2"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", local, "-vf", vf,
                    "-fps_mode", "vfr", "-frames:v", str(MAX_FRAMES), os.path.join(d, "%03d.jpg")], check=False)
    return sorted(glob.glob(os.path.join(d, "*.jpg")))


def asr(wav):
    rec = Recognition(model=ASR_MODEL, callback=None, format="wav",
                      sample_rate=16000, language_hints=["zh", "en"])
    res = rec.call(wav)
    if res.status_code != 200:
        log("ASR 失败", res.status_code, res.message)
        return ""
    sents = res.get_sentence() or []
    if isinstance(sents, dict):
        sents = [sents]
    return "".join(s.get("text", "") for s in sents).strip()


def _vl_one(frame):
    with open(frame, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = MultiModalConversation.call(model=VLM_MODEL, messages=[{"role": "user", "content": [
        {"image": f"data:image/jpeg;base64,{b64}"}, {"text": VL_PROMPT}]}])
    if r.status_code != 200:
        log("VL 失败", r.status_code, r.message)
        return ""
    return r.output.choices[0].message.content[0]["text"].strip()


def visual_extract(frames):
    from concurrent.futures import ThreadPoolExecutor
    # 并发跑所有帧的 qwen-vl（保持帧顺序），再按序模糊去重
    with ThreadPoolExecutor(max_workers=int(os.environ.get("VL_CONCURRENCY", "6"))) as ex:
        texts = list(ex.map(_vl_one, frames))
    norm = lambda s: "".join(s.split())
    blocks, prev, kept = [], None, 0
    for txt in texts:
        txt = (txt or "").strip()
        if not txt or txt == "(无新信息)":
            continue
        if prev and difflib.SequenceMatcher(None, norm(txt), norm(prev)).ratio() >= 0.90:
            continue
        prev = txt
        kept += 1
        blocks.append(f"[画面 {kept}]\n{txt}")
    return "\n\n".join(blocks)


def summarize(transcript, visual, link, today):
    system = "你是笔记整理助手。只输出一则 Obsidian markdown 笔记本身，不要任何额外说明，不要用代码围栏包裹。"
    prompt = f"""把下面的【语音转写】和【画面提取】整合成一则 Obsidian 笔记。
要求：
- 整篇笔记只有三个二级标题，顺序固定：`## 摘要`、`## 要点`、`## 全文`。
- `## 全文` 是把内容按段落展开的正文（可用少量 ### 小标题分节），但**绝不重复 title/摘要/要点**，也不要在全文里再写"标题""摘要""要点"这些小标题。
- 画面提取来自视频关键帧识别，可能有重复或噪声；若语音转写为空则完全依据画面；去重整合，信息准确为先。
- 标签(tags)只用单个词，英文多词用连字符连接，不要空格。

严格按此结构输出（不要多加别的二级标题）：
---
title: 一句话标题
date: {today}
source: {link}
tags: [视频号, 主题标签2到4个]
---
## 摘要
三句话以内
## 要点
- 分点，每点一句
## 全文
按逻辑顺序整理的正文段落

【语音转写】
{transcript or '（无有效人声）'}

【画面提取】
{visual or '（无）'}"""
    r = Generation.call(model=TEXT_MODEL, result_format="message",
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": prompt}])
    if r.status_code != 200:
        raise RuntimeError(f"摘要失败 {r.status_code} {r.message}")
    return r.output.choices[0].message.content.strip()


# ---------- markdown 清洗（与本地 clip.sh 等价）----------
def strip_fences(md):
    lines = md.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def fix_frontmatter(md):
    lines = md.splitlines()
    if lines and re.match(r"^---\s*$", lines[0]):
        for i in range(1, len(lines)):
            if re.match(r"^---\s*$", lines[i]):
                break
            if lines[i].startswith("#"):
                lines.insert(i, "---")
                break
    return "\n".join(lines)


def fix_tags(md):
    def repl(m):
        items = [re.sub(r"\s+", "-", it.strip()) for it in m.group(1).split(",") if it.strip()]
        return "tags: [" + ", ".join(items) + "]"
    return re.sub(r"(?m)^tags:\s*\[(.*?)\]", repl, md)


def slugify_title(md):
    m = re.search(r"^title:\s*(.+)$", md, re.M)
    t = (m.group(1).strip() if m else "")
    t = re.sub(r'[/\\:*?"<>|\n\r\t]+', " ", t).strip().strip(".")
    return t[:40]


def process(url, today=None):
    """完整管道：返回 (vault_path, markdown)。"""
    today = today or datetime.date.today().isoformat()
    src, desc = resolve_link(url)
    if not src:
        raise RuntimeError("快路径未拿到直链（cookie 失效/私密视频？）")
    log("直链", src[:80], "| 描述", desc[:30])
    with tempfile.TemporaryDirectory() as wd:
        local = download_video(src, wd)
        if not local:
            raise RuntimeError("视频下载失败（CDN 不可达或超时）")
        transcript = ""
        if VISUAL_MODE != "visual":
            wav = extract_audio(local, wd)
            if wav:
                transcript = asr(wav)
            log("ASR", len(transcript), "字")
        visual = ""
        if VISUAL_MODE != "audio":
            frames = extract_frames(local, wd)
            log("关键帧", len(frames))
            if frames:
                visual = visual_extract(frames)
        if not transcript.strip() and not visual.strip():
            raise RuntimeError("音频和画面都没提取到内容")
        md = summarize(transcript, visual, url, today)
    md = fix_tags(fix_frontmatter(strip_fences(md)))
    title = slugify_title(md)
    name = f"{today.replace('-', '')}-{title}" if title else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return name, md
