#!/usr/bin/env python3
# 视觉理解：对视频关键帧做 Apple Vision OCR + 本地 VLM 理解，拼成文本打到 stdout
# 契约与 asr_*.py 一致：python3 visual_extract.py <帧目录> → 纯文本到 stdout（日志走 stderr）
# 依赖：pip install ocrmac（Apple Vision OCR）；VLM 二选一：
#   - mlx（默认）：pip install mlx-vlm；模型 mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
#   - ollama：ollama + 一个视觉模型（如 qwen2.5vl:7b）
# 环境变量：
#   VLM_BACKEND  mlx(默认) | ollama
#   VLM_MODEL    mlx 仓库名 或 ollama 模型名（不设则按后端取默认）
#   VISUAL_OCR   1/0 是否启用 OCR（默认 1）
#   VISUAL_VLM   1/0 是否启用 VLM（默认 1；不可用自动降级只 OCR）
#   OLLAMA_HOST  ollama 地址（默认 127.0.0.1:11434）
import base64
import difflib
import json
import os
import sys
import urllib.request

MLX_DEFAULT = "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
OLLAMA_DEFAULT = "qwen2.5vl:7b"
VLM_PROMPT = (
    "这是视频中的一帧，通常是幻灯片或信息卡。用中文提取画面里的关键信息："
    "标题、要点文字、图表或数据的含义。若是纯装饰/转场画面或没有实质信息，"
    "只回复“(无新信息)”。只输出提取到的内容，不要客套话。"
)

def log(*a):
    print(*a, file=sys.stderr, flush=True)

def list_frames(d):
    exts = (".jpg", ".jpeg", ".png")
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(exts))

def ocr_image(path):
    from ocrmac import ocrmac
    anns = ocrmac.OCR(path, language_preference=["zh-Hans", "en-US"],
                      recognition_level="accurate").recognize()
    return "\n".join(t.strip() for (t, c, _b) in anns if t and t.strip())

def norm(s):
    return "".join(s.split())

def ollama_url():
    h = os.environ.get("OLLAMA_HOST", "").strip()
    if not h:
        return "http://127.0.0.1:11434"
    if not h.startswith("http"):
        h = "http://" + h
    h = h.replace("0.0.0.0", "127.0.0.1")
    from urllib.parse import urlparse
    if not urlparse(h).port:
        h = h.rstrip("/") + ":11434"
    return h.rstrip("/")

# ---- VLM 后端：返回 caption(path)->str 或 None（不可用）----
def make_vlm():
    if os.environ.get("VISUAL_VLM", "1") == "0":
        return None, "off"
    backend = os.environ.get("VLM_BACKEND", "mlx").lower()

    if backend == "ollama":
        model = os.environ.get("VLM_MODEL") or OLLAMA_DEFAULT
        host = ollama_url()
        try:
            with urllib.request.urlopen(host + "/api/tags", timeout=5) as r:
                tags = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
            if not any(t == model or t.startswith(model.split(":")[0]) for t in tags):
                log(f"[visual] Ollama 模型 {model} 未就绪，降级只 OCR"); return None, "ollama-missing"
        except Exception as e:
            log(f"[visual] Ollama 不可用（{e}），降级只 OCR"); return None, "ollama-down"

        def cap(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload = json.dumps({"model": model, "prompt": VLM_PROMPT, "images": [b64],
                                  "stream": False, "keep_alive": "30m",
                                  "options": {"temperature": 0.1}}).encode()
            req = urllib.request.Request(host + "/api/generate", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read()).get("response", "").strip()
        return cap, f"ollama:{model}"

    # mlx（默认）
    model_id = os.environ.get("VLM_MODEL") or MLX_DEFAULT
    try:
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
        log(f"[visual] 加载 MLX 模型 {model_id}（首次下载约 18GB，之后走本地缓存）…")
        model, processor = load(model_id)
        config = load_config(model_id)

        def cap(path):
            pr = apply_chat_template(processor, config, VLM_PROMPT, num_images=1)
            r = generate(model, processor, pr, image=[path], verbose=False, max_tokens=512)
            return (r.text if hasattr(r, "text") else str(r)).strip()
        log("[visual] MLX 模型就绪")
        return cap, f"mlx:{model_id}"
    except Exception as e:
        log(f"[visual] MLX 加载失败（{e}），降级只 OCR")
        return None, "mlx-failed"

def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit("用法: python3 visual_extract.py <帧目录>")
    frames = list_frames(sys.argv[1])
    if not frames:
        sys.exit("帧目录为空")

    use_ocr = os.environ.get("VISUAL_OCR", "1") != "0"
    vlm_cap, vlm_tag = make_vlm()
    log(f"[visual] {len(frames)} 帧；OCR={use_ocr} VLM={vlm_tag}")

    blocks, prev_ocr, kept = [], None, 0
    for i, fp in enumerate(frames, 1):
        ocr_txt = ""
        if use_ocr:
            try:
                ocr_txt = ocr_image(fp)
            except Exception as e:
                log(f"[visual] 帧{i} OCR 失败: {e}")
        # 去重：与上一保留帧 OCR 高度相似(≥0.90)则跳过——容忍 OCR 抖动，折叠同一张卡的重复帧
        if ocr_txt and prev_ocr is not None and \
           difflib.SequenceMatcher(None, norm(ocr_txt), norm(prev_ocr)).ratio() >= 0.90:
            continue
        vlm_txt = ""
        if vlm_cap:
            try:
                vlm_txt = vlm_cap(fp)
            except Exception as e:
                log(f"[visual] 帧{i} VLM 失败: {e}")
        if not (ocr_txt or vlm_txt):
            continue
        if ocr_txt:
            prev_ocr = ocr_txt
        kept += 1
        part = [f"[画面 {kept}]"]
        if ocr_txt:
            part.append("文字：\n" + ocr_txt)
        if vlm_txt and vlm_txt != "(无新信息)":
            part.append("理解：" + vlm_txt)
        blocks.append("\n".join(part))
        log(f"[visual] 帧{i} → 画面{kept}")

    if not blocks:
        sys.exit("视觉理解未提取到内容")
    print("\n\n".join(blocks))

if __name__ == "__main__":
    main()
