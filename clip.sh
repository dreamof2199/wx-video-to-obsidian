#!/usr/bin/env bash
# 视频号 → Obsidian 一键管道（手动触发版）
# 用法:
#   clip.sh --file <mp4路径>          # 主路径：下载器拿到的 mp4 直接进管道
#   clip.sh --last                    # 主路径：自动取 WXD_DOWNLOAD_DIR 里最新的 mp4
#   clip.sh <视频号分享链接>          # 快路径：经 Cloudflare Worker 解析直链（需 SPH_API）
#   clip.sh --doctor                  # 自检：依赖/下载器/ASR/vault 是否就绪
#   clip.sh --help
#
# 配置全部可用环境变量覆盖（无需改本文件）：
#   VAULT  WXD_DOWNLOAD_DIR(--last 取最新mp4的目录)  ASR_PYTHON ASR_BACKEND(sensevoice|dashscope|mlxwhisper) ASR_LANGUAGE ASR_DEVICE
#   快路径: SPH_API(workers.dev地址) WXD_API PARSE_PATH
#   云端 ASR: DASHSCOPE_API_KEY DASHSCOPE_ENV_FILE DASHSCOPE_MODEL_ASR
#   视觉理解: VISUAL_MODE(both|audio|visual) VLM_MODEL VISUAL_MAX_FRAMES SCENE_THRESHOLD
set -euo pipefail

# ========= 配置（环境变量优先，否则用默认）=========
: "${WXD_API:=http://127.0.0.1:2022}"        # 本地下载器的 HTTP 服务（主路径：判断它是否在运行）
: "${SPH_API:=}"                             # 快路径：已部署的视频号查询 Worker 地址 https://<名>.<子域>.workers.dev（见 SETUP.md §C）
: "${PARSE_PATH:=/api/fetch_video_profile}"  # 快路径端点(POST {"url":...})，由上面的 Worker 提供
: "${INBOX:=$HOME/VideoInbox}"               # 中转目录
: "${WXD_DOWNLOAD_DIR:=$HOME/Downloads}"     # 下载器 config.yaml 的 download.dir 落点（--last 从这里取最新 mp4）
: "${VAULT:=$HOME/Obsidian/Vault/Inbox}"     # Obsidian 收件夹（落盘即收录）
: "${ASR_BACKEND:=sensevoice}"               # sensevoice(本地离线,默认) | dashscope(云端百炼Fun-ASR) | mlxwhisper
: "${ASR_PY:=$HOME/bin/asr_sensevoice.py}"   # SenseVoice 封装脚本路径
: "${ASR_DASHSCOPE_PY:=$HOME/bin/asr_dashscope.py}"  # 云端 ASR 封装脚本路径
: "${ASR_LANGUAGE:=zh}"                       # 传给 ASR 的语言；中英混说可设 auto
: "${VISUAL_MODE:=both}"                      # both(默认:音频+视觉) | audio(仅ASR) | visual(仅画面)
: "${VISUAL_PY:=$HOME/bin/visual_extract.py}" # 视觉理解脚本（Apple Vision OCR + 本地 VLM）
: "${VLM_BACKEND:=ollama}"                    # ollama(默认,保温快) | mlx
: "${VLM_MODEL:=qwen3-vl:30b-a3b}"            # 本地 VLM（Qwen3-VL-30B-A3B）；不可用自动降级只 OCR
: "${VISUAL_MAX_FRAMES:=60}"                  # 关键帧上限
: "${SAMPLE_FPS:=1}"                          # 定时抽帧帧率（再用 mpdecimate 确定性去重，每张卡留一帧）
: "${MPDECIMATE:=hi=64*48:lo=64*16:frac=0.02}" # mpdecimate 去重灵敏度（文字卡背景相似，调灵敏）

die() { echo "✗ $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "缺少依赖：$1${2:+（$2）}"; }

# 挑一个能 import 指定模块的 python（用户 PATH 的 python3 常不是装了 funasr/dashscope 的那个）
pick_python() {
  local mod="$1" p
  for p in ${ASR_PYTHON:+"$ASR_PYTHON"} python3 ${CONDA_PREFIX:+"$CONDA_PREFIX/bin/python3"} \
           "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" "$HOME/miniforge3/bin/python3"; do
    { command -v "$p" >/dev/null 2>&1 || [ -x "$p" ]; } || continue
    "$p" -c "import $mod" >/dev/null 2>&1 && { printf '%s' "$p"; return 0; }
  done
  return 1
}

# ========= 自检 =========
doctor() {
  echo "配置："
  printf '  %-13s %s\n' WXD_API "$WXD_API" SPH_API "${SPH_API:-（未设，快路径关）}" \
    PARSE_PATH "$PARSE_PATH" VAULT "$VAULT" ASR_BACKEND "$ASR_BACKEND"
  echo "依赖："
  for c in ffmpeg jq curl python3 claude; do
    if command -v "$c" >/dev/null 2>&1; then echo "  ✓ $c"; else echo "  ✗ $c 缺失"; fi
  done
  echo "ASR 后端（${ASR_BACKEND}）："
  case "$ASR_BACKEND" in
    dashscope)
      p="$(pick_python dashscope)" && echo "  ✓ dashscope（$p）" || echo "  ✗ 没有装了 dashscope 的 python（pip install dashscope）"
      [ -f "$ASR_DASHSCOPE_PY" ] && echo "  ✓ $ASR_DASHSCOPE_PY" || echo "  ✗ 找不到 $ASR_DASHSCOPE_PY"
      if python3 - >/dev/null 2>&1 <<'PY'
import os,sys
sys.path.insert(0, os.path.expanduser("~/bin"))
from asr_dashscope import load_api_key
load_api_key()
PY
      then echo "  ✓ DASHSCOPE_API_KEY 可读"; else echo "  ✗ 取不到 DASHSCOPE_API_KEY（设环境变量或 DASHSCOPE_ENV_FILE）"; fi ;;
    sensevoice)
      p="$(pick_python funasr)" && echo "  ✓ funasr（$p）" || echo "  ✗ 没有装了 funasr 的 python（pip install funasr torch torchaudio）"
      [ -f "$ASR_PY" ] && echo "  ✓ $ASR_PY" || echo "  ✗ 找不到 $ASR_PY" ;;
    mlxwhisper)
      command -v mlx_whisper >/dev/null 2>&1 && echo "  ✓ mlx_whisper" || echo "  ✗ mlx_whisper 缺失（pip install mlx-whisper）" ;;
    *) echo "  ✗ 未知 ASR_BACKEND：$ASR_BACKEND" ;;
  esac
  if [ "$VISUAL_MODE" != "audio" ]; then
    echo "视觉理解（VISUAL_MODE=${VISUAL_MODE}）："
    p="$(pick_python ocrmac)" && echo "  ✓ ocrmac（$p）" || echo "  ✗ 没有装了 ocrmac 的 python（pip install ocrmac）"
    [ -f "$VISUAL_PY" ] && echo "  ✓ $VISUAL_PY" || echo "  ✗ 找不到 $VISUAL_PY"
    if [ "$VLM_BACKEND" = "ollama" ]; then
      if curl -s -m 3 http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "$VLM_MODEL"; then
        echo "  ✓ VLM(ollama) $VLM_MODEL 就绪"
      else echo "  ✗ VLM(ollama) $VLM_MODEL 未就绪（ollama pull $VLM_MODEL）——自动降级只 OCR"; fi
    else
      pv="$(pick_python mlx_vlm)" && {
        if "$pv" - <<PY >/dev/null 2>&1
import os,glob,sys
m="$VLM_MODEL".split("/")[-1]
h=os.path.expanduser("~/.cache/huggingface/hub")
sys.exit(0 if glob.glob(os.path.join(h,f"*{m}*")) else 1)
PY
        then echo "  ✓ VLM(mlx) $VLM_MODEL 已缓存"; else echo "  ✗ VLM(mlx) $VLM_MODEL 未下载——首次运行会自动下(~18GB)，或降级只 OCR"; fi
      } || echo "  ✗ 没有装了 mlx-vlm 的 python（pip install mlx-vlm）——自动降级只 OCR"
    fi
  fi
  echo "取视频途径："
  if curl -sf -m 3 "$WXD_API" >/dev/null 2>&1; then
    echo "  ✓ 主路径：本地下载器 $WXD_API 在运行（微信播放→「下载」按钮拿 mp4 → --file）"
  else
    echo "  ✗ 主路径：本地下载器未运行（需先启动它，见 SETUP.md §B）"
  fi
  if [ -n "$SPH_API" ]; then
    if curl -sf -m 5 "$SPH_API" >/dev/null 2>&1; then
      echo "  ✓ 快路径：Worker $SPH_API 可达（clip.sh <分享链接>）"
    else
      echo "  ✗ 快路径：Worker $SPH_API 不可达（确认已 sph_deploy / 地址正确）"
    fi
  else
    echo "  – 快路径：未启用（未设 SPH_API；如需见 SETUP.md §C）"
  fi
  echo "Obsidian 收件夹："
  if mkdir -p "$VAULT" 2>/dev/null && [ -w "$VAULT" ]; then echo "  ✓ $VAULT 可写"; else echo "  ✗ $VAULT 不可写"; fi
}

# ========= 快路径：分享链接 → 视频直链（经已部署的 Cloudflare Worker）=========
# 契约已对照源码核实（ltaoo/wx_channels_download：internal/api/sph/{worker.js,index.html}）：
#   POST $SPH_API/api/fetch_video_profile  body {"url":"<分享链接>"}
#   直链字段 = .data.feedInfo.h264VideoInfo.videoUrl（退 h265 → videoUrl），明文可被 ffmpeg 直读。
# 前置：wx_video_download sph_deploy 部署 Worker，并 export SPH_API=<workers.dev 地址>（SETUP.md §C）。
LAST_RESP=""
extract_src() {
  printf '%s' "$1" | jq -r '
    .data.feedInfo.h264VideoInfo.videoUrl
    // .data.feedInfo.h265VideoInfo.videoUrl
    // .data.feedInfo.videoUrl
    // empty' 2>/dev/null || true
}
resolve_link() {
  local link="$1" resp src
  [ -n "$SPH_API" ] || return 1                                          # 未启用快路径
  resp="$(curl -s -m 30 -X POST "${SPH_API%/}$PARSE_PATH" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg u "$link" '{url:$u}')" || true)"
  src="$(extract_src "$resp")"
  LAST_RESP="$resp"
  [ -n "$src" ] && printf '%s' "$src"
}

# ========= claude 输出清洗 =========
strip_fences() {  # 去掉 claude 可能包裹的 ``` 围栏
  awk '{l[n++]=$0} END{s=0;e=n-1;
    if(l[s]~/^```/)s++; if(e>=s&&l[e]~/^```[[:space:]]*$/)e--;
    for(i=s;i<=e;i++)print l[i]}'
}
fix_frontmatter() {  # 保证 frontmatter 闭合：claude 偶尔漏掉第二个 ---，进正文(# 标题)前补上
  awk 'NR==1 && /^---[[:space:]]*$/{print; fm=1; next}
    fm==1 && /^---[[:space:]]*$/{print; fm=0; next}
    fm==1 && /^#/{print "---"; print; fm=0; next}
    {print}'
}
fix_tags() {  # Obsidian 标签不能含空格：把 tags:[...] 里每个标签内部空格→连字符（AI Agent→AI-Agent）
  awk '/^tags:[[:space:]]*\[.*\]/{
      s=$0; lb=index(s,"["); rb=index(s,"]"); inner=substr(s,lb+1,rb-lb-1);
      n=split(inner,a,","); out="";
      for(i=1;i<=n;i++){ t=a[i];
        gsub(/^[[:space:]]+|[[:space:]]+$/,"",t); gsub(/[[:space:]]+/,"-",t);
        if(t!="") out=out (out==""?"":", ") t; }
      print "tags: [" out "]"; next }
    {print}'
}
set_source() {  # 仅替换 frontmatter（首个 --- 块）内的首行 source:
  awk -v link="$1" 'BEGIN{fm=0;done=0}
    /^---[[:space:]]*$/{fm++; print; next}
    fm==1 && !done && /^source:/{print "source: " link; done=1; next}
    {print}'
}

# ========= 入口分发 =========
case "${1:-}" in
  --help|-h|"") sed -n '2,14p' "$0"; exit 0 ;;
  --doctor)     doctor; exit 0 ;;
esac

need ffmpeg "brew install ffmpeg"
need jq "brew install jq"
need curl
need python3

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$INBOX" "$VAULT"
base="$(date +%Y%m%d-%H%M%S)"
src=""; link=""

# ========= 入参解析 =========
if [ "$1" = "--file" ]; then
  src="${2:?用法: clip.sh --file <已解密的mp4路径>}"
  [ -f "$src" ] || die "找不到文件：$src"
elif [ "$1" = "--last" ]; then
  # 取下载目录里最新的 mp4（配合下载器 config.yaml 的 download.dir）
  src="$(ls -t "$WXD_DOWNLOAD_DIR"/*.mp4 2>/dev/null | head -1 || true)"
  [ -n "$src" ] || die "在 $WXD_DOWNLOAD_DIR 没找到 mp4。先用下载器下载，或设 WXD_DOWNLOAD_DIR 指向 download.dir"
  echo "→ 最新文件：$src" >&2
else
  link="$1"
  [ -n "$SPH_API" ] || die "快路径需先部署 Worker 并 export SPH_API=<https://xxx.workers.dev>（见 SETUP.md §C）；或改走主路径 clip.sh --file <mp4>"
  src="$(resolve_link "$link" || true)"
  if [ -z "$src" ]; then
    echo "✗ 快路径未拿到直链。常见原因：sphCookie 失效、Worker 未正确部署，或私密视频。" >&2
    echo "  请求：POST ${SPH_API%/}$PARSE_PATH" >&2
    echo "  原始返回：${LAST_RESP:-（无响应，确认 SPH_API 可达且已 sph_deploy）}" >&2
    echo "  改走主路径：保持下载器运行 → 微信 PC 客户端播放该视频 → 点页面「下载」按钮得 mp4，" >&2
    echo "        再运行：clip.sh --file <那个mp4>（详见 SETUP.md §B）" >&2
    exit 2
  fi
fi

# http(s) 直链（快路径，微信 CDN）加 UA；本地文件不能带该选项
ua_opt=()
case "$src" in
  http://*|https://*) ua_opt=(-user_agent "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36") ;;
esac

# ========= 音频分支：抽 16k 音轨 → ASR（both/audio；无人声不致命，留给视觉兜底）=========
txt=""
if [ "$VISUAL_MODE" != "visual" ]; then
  wav="$WORK/$base.wav"
  if ffmpeg -nostdin -loglevel error ${ua_opt[@]+"${ua_opt[@]}"} -i "$src" -vn -ar 16000 -ac 1 "$wav" 2>/dev/null && [ -s "$wav" ]; then
    case "$ASR_BACKEND" in
      dashscope)
        [ -f "$ASR_DASHSCOPE_PY" ] || die "找不到 ASR 脚本：$ASR_DASHSCOPE_PY"
        PY="$(pick_python dashscope)" || die "找不到装了 dashscope 的 python（pip install dashscope）"
        txt="$("$PY" "$ASR_DASHSCOPE_PY" "$wav" 2>/dev/null || true)" ;;
      sensevoice)
        [ -f "$ASR_PY" ] || die "找不到 ASR 脚本：$ASR_PY"
        PY="$(pick_python funasr)" || die "找不到装了 funasr 的 python（pip install funasr torch torchaudio）"
        txt="$(ASR_LANGUAGE="$ASR_LANGUAGE" "$PY" "$ASR_PY" "$wav" 2>/dev/null || true)" ;;
      mlxwhisper)
        need mlx_whisper "pip install mlx-whisper"
        mlx_whisper "$wav" --model mlx-community/whisper-large-v3-turbo \
          --language "$ASR_LANGUAGE" --output-format txt --output-dir "$WORK" >/dev/null 2>&1 || true
        txt="$(cat "$WORK/$base.txt" 2>/dev/null || true)" ;;
      *) die "未知 ASR_BACKEND: $ASR_BACKEND" ;;
    esac
  else
    echo "→ 无音轨或抽取失败，跳过 ASR" >&2
  fi
  [ -n "${txt//[[:space:]]/}" ] && echo "→ ASR 完成" >&2 || echo "→ ASR 无有效人声（将靠画面）" >&2
fi

# ========= 视觉分支：场景检测抽关键帧 → OCR + 本地 VLM（both/visual）=========
vtxt=""
if [ "$VISUAL_MODE" != "audio" ]; then
  [ -f "$VISUAL_PY" ] || die "找不到视觉脚本：$VISUAL_PY"
  frames="$WORK/frames"; mkdir -p "$frames"
  # 确定性键帧：定时抽帧 fps → mpdecimate 丢弃近重复帧（每张卡/每个画面状态留一帧）。
  # 比场景检测稳——文字卡背景相似时场景分数上不去，会漏抽。
  ffmpeg -nostdin -loglevel error ${ua_opt[@]+"${ua_opt[@]}"} -i "$src" \
    -vf "fps=$SAMPLE_FPS,mpdecimate=$MPDECIMATE,scale='min(1280,iw)':-2" \
    -fps_mode vfr -frames:v "$VISUAL_MAX_FRAMES" "$frames/%03d.jpg" 2>/dev/null || true
  nframes="$(ls "$frames"/*.jpg 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${nframes:-0}" -gt 0 ]; then
    echo "→ 抽到 $nframes 关键帧，OCR+VLM 提取中（本地，约数秒/帧）…" >&2
    PYV="$(pick_python ocrmac)" || die "找不到装了 ocrmac 的 python（pip install ocrmac）"
    vtxt="$(VLM_BACKEND="$VLM_BACKEND" VLM_MODEL="$VLM_MODEL" "$PYV" "$VISUAL_PY" "$frames" 2>>"$WORK/visual.log" || true)"
    [ -n "${vtxt//[[:space:]]/}" ] && echo "→ 视觉提取完成" >&2 || echo "→ 视觉未提取到内容" >&2
  else
    echo "→ 未抽到关键帧（纯音频/无画面变化）" >&2
  fi
fi

[ -n "${txt//[[:space:]]/}" ] || [ -n "${vtxt//[[:space:]]/}" ] || \
  die "音频和画面都没提取到内容（无有效人声，也无可识别画面）"

# ========= 摘要（Claude headless；整合语音转写 + 画面提取）=========
need claude
prompt="你是笔记整理助手。根据下面的【语音转写】和【画面提取】整合成一则 Obsidian 笔记，直接输出 markdown，不要任何额外说明，不要用代码围栏包裹。
说明：画面提取来自视频关键帧的 OCR 文字与 VLM 理解，可能有重复或识别噪声；若语音转写为空则完全依据画面；两者请去重整合，信息准确为先。
标签(tags)只用单个词，英文多词用连字符连接，不要空格。
格式：
---
title: 一句话标题
date: $(date +%F)
source: 占位
tags: [视频号, 主题标签2到4个]
---
## 摘要
三句话以内
## 要点
- 分点，每点一句
## 全文
按逻辑顺序整理内容，修正明显错别字与标点、不改原意；以画面信息为主时按画面顺序组织

【语音转写】
${txt:-（无有效人声）}

【画面提取】
${vtxt:-（无）}"

# 用 --output-format json 拿回 markdown 结果 + token 用量；瞬时 403/限流自动重试
resp=""; md=""
for attempt in 1 2 3; do
  resp="$(printf '%s' "$prompt" | claude -p --output-format json 2>/dev/null || true)"
  md="$(printf '%s' "$resp" | jq -r '(if type=="array" then .[] else . end)|select(.type=="result")|.result // empty' 2>/dev/null)"
  [ -n "${md//[[:space:]]/}" ] && break
  [ "$attempt" -lt 3 ] && { echo "→ claude 第${attempt}次未成功，3s 后重试…" >&2; sleep 3; }
done
[ -n "${md//[[:space:]]/}" ] || die "claude -p 多次调用失败（登录/额度/限流？）。终端测 'claude -p hi'。返回片段：$(printf '%s' "$resp" | head -c 200)"
md="$(printf '%s' "$md" | strip_fences | fix_frontmatter | fix_tags)"
[ -n "$link" ] && md="$(printf '%s' "$md" | set_source "$link")"

# token 用量（本地 ASR 不计；input 含 cache 读/写）
usage="$(printf '%s' "$resp" | jq -c '(if type=="array" then .[] else . end)|select(.type=="result")|.usage' 2>/dev/null | head -1)"
in_tok="$(printf '%s' "$usage" | jq -r '((.input_tokens//0)+(.cache_read_input_tokens//0)+(.cache_creation_input_tokens//0))' 2>/dev/null)"
out_tok="$(printf '%s' "$usage" | jq -r '(.output_tokens//0)' 2>/dev/null)"
cost="$(printf '%s' "$resp" | jq -r '(if type=="array" then .[] else . end)|select(.type=="result")|.total_cost_usd // empty' 2>/dev/null | head -1)"

# ========= 写入 Obsidian（文件名=日期-标题，标题去非法字符并限长 40 字，防覆盖）=========
title="$(printf '%s' "$md" | python3 -c 'import sys,re
m=re.search(r"^title:\s*(.+)$", sys.stdin.read(), re.M)
t=(m.group(1).strip() if m else "")
t=re.sub(r"[/\\:*?\"<>|\n\r\t]+"," ",t).strip().strip(".")
print(t[:40])' 2>/dev/null || true)"
[ -n "$title" ] && name="$(date +%Y%m%d)-$title" || name="$base"
out="$VAULT/$name.md"; i=1
while [ -e "$out" ]; do out="$VAULT/$name-$i.md"; i=$((i+1)); done
printf '%s\n' "$md" > "$out"
echo "✓ 已写入 $out"
[ -n "${in_tok:-}" ] && [ "$in_tok" != "null" ] && \
  echo "📊 本次 Claude token：输入 ${in_tok} + 输出 ${out_tok}（本地 ASR 不计）${cost:+　按 API 价≈\$${cost}}"
