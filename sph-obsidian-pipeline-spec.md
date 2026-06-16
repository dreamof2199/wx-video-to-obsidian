# 视频号 → Obsidian 自动化管道 · 实现规格

> 交接文档。设计已定，实现者（Claude Code）的任务是：落地、对着真实工具**验证 §5 的三个未确认事实**、加固。
> 三条硬约束，请勿违反：
> 1. 采集只用到必要的边界动作，**下游全部是确定性 shell/python，不要 agentic 循环、不要 computer-use**。
> 2. **不要臆测 `parse_sph` 的请求方法与响应字段**——先按 §5 验证，再写代码。
> 3. 手动触发、不实时监听。不要加 fswatch / 守护进程 / 定时轮询。

---

## 1. 目标与约束

- **输入**：用户手动把视频号视频转发到文件传输助手，并复制其分享链接（形如 `https://weixin.qq.com/sph/xxxx`）。
- **输出**：Obsidian vault 收件夹里一则 markdown，含 YAML frontmatter + 三句摘要 + 分点要点 + 整理后的全文转写。
- **运行环境**：Apple Silicon Mac（M4 Max MacBook / Mac mini M4）。已安装并登录 Claude Code（`claude -p` 可用）。
- **成本原则**：采集本地、ASR 本地（零成本）、摘要走 Claude 订阅额度（配额内边际成本≈0）。

---

## 2. 选型决策（已定，无需重新评估）

| 环节 | 选择 | 理由 |
|---|---|---|
| 采集 | **wx_channels_download (ltaoo)** | 有 CLI（`download`/`decrypt`）+ HTTP API（含 `parse_sph`），单 8MB 二进制、终端原生，适合接管道。 |
| —（弃用） | ~~res-downloader (putyy)~~ | Wails GUI、面向大众、无自动化接口；脚本化只能 UI 自动化，违背约束。 |
| ASR | **SenseVoice-Small（FunASR，达摩院）** 默认 | 中文识别最稳、自带标点/ITN、速度快。备选 mlx-whisper large-v3-turbo。 |
| 摘要 | **`claude -p` headless** | 已付费、质量最好。本地 Qwen（Ollama）为超配额/离线兜底（可选）。 |
| 入库 | 直接写 vault 目录文件 | Obsidian 把 vault 当普通文件夹，落盘即收录。 |
| 砍掉 | computer-use 层、实时监听 | 在"手动触发"约束下不需要。 |

---

## 3. 架构与数据流

两条采集路径，汇入同一条下游管道。

```
快路径（默认，无需播放/解密）
  分享链接 ──> parse_sph(本地, 需 sphCookie) ──> 视频直链(未加密)
                                                      │
兜底路径（parse_sph 失败时）                          │
  分享链接 ──> PC微信播放(下载器代理开启)             │
            ──> 「更多→打印下载命令」                 │
            ──> wx download --url --key ──> 解密mp4 ──┤
                                                      ▼
              ffmpeg 抽 16k 单声道音轨 ──> ASR(SenseVoice) ──> claude -p 摘要 ──> 写 Obsidian md
```

下游管道对两条路径无差别：拿到一个可被 ffmpeg 读取的源（http 直链 或 本地 mp4），其余完全相同。

---

## 4. 组件与契约

- **`clip.sh`**（编排入口，参考实现见附录 A）
  - 入参：`clip.sh <分享链接>`（快路径）或 `clip.sh --file <mp4>`（兜底入口）。
  - 职责：解析直链 / 接收本地文件 → ffmpeg 抽音轨 → 调 ASR → 调 `claude -p` → 写 vault。
  - 命名：按时间戳，避免覆盖。
- **`asr_sensevoice.py`**（参考实现见附录 B）
  - 契约：`python3 asr_sensevoice.py <wav>` → 规整后的纯文本转写打印到 stdout。
- **wx_channels_download**：常驻 HTTP 服务（端口见 §5）+ CLI（`download`/`decrypt`/`sph_deploy`/`install`/`uninstall`）。
- **`config.yaml`**：`cloudflare.sphCookie`（来自 yuanbao.tencent.com 登录后的 cookie），是 `parse_sph` 的唯一前置。

---

## 5. ⚠️ 实现前必须核对的三个事实

**这三条未在文档层面确认，必须对着运行中的工具实测后再写死。**

1. **HTTP 服务端口**
   项目 issue 中出现的是 `http://127.0.0.1:2022`。启动下载器后确认实际端口（看启动日志或 `lsof -iTCP -sTCP:LISTEN -P | grep wx`）。`clip.sh` 顶部 `WXD_API` 据此修改。

2. **`parse_sph` 的请求/响应契约（最关键）**
   未确认是 `GET ?url=` 还是 `POST` body；响应 JSON 字段名未知。验证：
   ```bash
   curl -s "http://127.0.0.1:2022/api/channels/parse_sph?url=https://weixin.qq.com/sph/xxxx" | jq .
   ```
   - 若 GET 返回非预期，试 POST：`curl -s -X POST .../parse_sph -H 'Content-Type: application/json' -d '{"url":"..."}' | jq .`
   - 看清真实结构后，把 `clip.sh` 里取直链的那行 jq 路径（当前是猜测的 `.data.url // .url // .data.video_url`）对齐到真实字段。
   - 注意返回可能是 m3u8 而非 mp4，ffmpeg 两者都能拉，但要在 T4 验证。

3. **`sphCookie` 有效性**
   `parse_sph` 不依赖播放、不依赖视频号页面，靠 `config.yaml` 里这段 cookie 在本地解析。cookie **会过期**，失效时快路径会失败并退到兜底。配好后重启下载器。

---

## 6. 安装与配置步骤（有序）

1. `brew install ffmpeg jq`
2. 下载 wx_channels_download 的 **Apple Silicon** 包；首次以管理员运行（自动装证书、起代理）。退出务必用 Ctrl+C，避免系统代理残留。
3. 配置 `config.yaml` 的 `cloudflare.sphCookie`（yuanbao.tencent.com 登录后取 cookie），重启下载器。
4. ASR 依赖二选一：
   - 默认：`pip install funasr`（SenseVoice 首次运行自动拉模型，约数百 MB）。
   - 备选：`pip install mlx-whisper`，并把 `clip.sh` 的 `ASR_BACKEND` 改为 `mlxwhisper`。
5. 放置脚本：`asr_sensevoice.py` → `~/bin/`；`clip.sh` → `~/bin/` 并 `chmod +x ~/bin/clip.sh`。
6. 改 `clip.sh` 顶部配置：`WXD_API`（端口）、`VAULT`（Obsidian 收件夹绝对路径）、`ASR_BACKEND`、`ASR_PY`。
7. 确认 `claude -p` 可直接出文本。

---

## 7. 测试计划

- **T1 快路径冒烟**：一条公开视频号链接 → `clip.sh <链接>` → 检查 vault 里生成 md，frontmatter 完整、`source` 已回填、`## 全文` 非空。
- **T2 字段对齐**：执行 §5.2 的 curl，确认 jq 路径与请求方法正确。
- **T3 兜底路径**：模拟 parse_sph 失败（清空/写错 sphCookie），确认脚本给出清晰提示并可走 `clip.sh --file <mp4>` 跑通。
- **T4 边界**：① m3u8 直链 ffmpeg 能否正常抽音轨；② 超长视频（>30 min）的 ASR 耗时/内存；③ 中英混说时把 `asr_sensevoice.py` 的 `language` 改 `auto` 的效果。
- **T5 cookie 过期**：手动令 cookie 失效，确认静默退回兜底且提示明确（不要静默吞错）。

---

## 8. 待你（Jin）拍板的决策

- **cookie 维护策略**：① 快路径为主 + 偶尔过期走兜底（推荐，免维护成本低）；② 纯兜底代理路径（最稳，但每条都要在微信里播放一次）。默认按 ①。
- **是否加**：按链接/视频 id 去重；一次传多条链接的批量模式；运行日志。默认都不加，保持最小。
- **摘要模型兜底**：是否加"超配额/离线时切本地 Qwen3 (Ollama)"分支。默认只用 Claude。

---

## 9. 阿里生态备选（可选，不进默认链路）

- **ASR 云端替代**：通义听悟——要钱要联网，仅当需要说话人分离又不想本地配 WhisperX 时考虑。
- **摘要本地化**：Qwen3-32B via Ollama，M4 Max 可跑，离线、不耗 Claude 配额。
- 这些都是 optional，默认链路保持"SenseVoice 本地 + Claude 摘要"。

---

## 附录 A · `clip.sh`（参考实现）

```bash
#!/usr/bin/env bash
# 视频号 → Obsidian 一键管道（手动触发版）
# 用法:
#   clip.sh <视频号分享链接>          # 快路径：parse_sph 解析直链，无需播放/解密
#   clip.sh --file <已解密的mp4路径>  # 兜底：本地文件直接进管道
set -euo pipefail

# ========= 配置（按需修改）=========
WXD_API="http://127.0.0.1:2022"          # wx_channels_download 的 HTTP 服务端口，启动后确认（§5.1）
INBOX="$HOME/VideoInbox"
VAULT="$HOME/Obsidian/Vault/Inbox"       # Obsidian 收件夹
ASR_BACKEND="sensevoice"                 # sensevoice | mlxwhisper
ASR_PY="$HOME/bin/asr_sensevoice.py"     # SenseVoice 封装脚本路径

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$INBOX" "$VAULT"
base="$(date +%Y%m%d-%H%M%S)"
src=""; link=""

# ========= 入参解析 =========
if [ "${1:-}" = "--file" ]; then
  src="${2:?用法: clip.sh --file <已解密的mp4路径>}"
else
  link="${1:?用法: clip.sh <视频号分享链接>}"
  # --- 快路径：parse_sph 解析直链 ---
  # 前置：config.yaml 配好 cloudflare.sphCookie（yuanbao.tencent.com 登录后取 cookie）
  # ⚠️ 下面 jq 字段路径是猜测，先按 §5.2 用 curl 看清真实结构再对齐：
  #   curl -s "$WXD_API/api/channels/parse_sph?url=<链接>" | jq .
  enc="$(printf '%s' "$link" | jq -sRr @uri)"
  resp="$(curl -s "$WXD_API/api/channels/parse_sph?url=$enc" || true)"
  src="$(printf '%s' "$resp" | jq -r '.data.url // .url // .data.video_url // empty' 2>/dev/null || true)"
  if [ -z "$src" ]; then
    echo "✗ parse_sph 未拿到直链。常见原因：sphCookie 未配/失效，或私密视频。"
    echo "  原始返回：$resp"
    echo "  兜底：PC 微信播放该视频（下载器代理开启）→「更多→打印下载命令」→ 执行得到解密 mp4，"
    echo "        再运行：clip.sh --file <那个mp4>"
    exit 2
  fi
fi

# ========= 抽 16k 单声道音轨（ASR 只需音频）=========
wav="$WORK/$base.wav"
ffmpeg -nostdin -loglevel error -i "$src" -vn -ar 16000 -ac 1 "$wav"

# ========= ASR =========
case "$ASR_BACKEND" in
  sensevoice)
    txt="$(python3 "$ASR_PY" "$wav")" ;;
  mlxwhisper)
    mlx_whisper "$wav" --model mlx-community/whisper-large-v3-turbo \
      --language zh --output-format txt --output-dir "$WORK" >/dev/null 2>&1
    txt="$(cat "$WORK/$base.txt")" ;;
  *) echo "未知 ASR_BACKEND: $ASR_BACKEND"; exit 3 ;;
esac

# ========= 摘要（用 Claude 订阅额度，headless）=========
prompt="你是笔记整理助手。把下面的视频转写整理成一则 Obsidian 笔记，直接输出 markdown，不要任何额外说明。
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
按段落整理转写，修正明显错别字与标点，不改原意

转写：
$txt"

md="$(printf '%s' "$prompt" | claude -p)"
[ -n "$link" ] && md="$(printf '%s' "$md" | sed "s|^source:.*|source: $link|")"

# ========= 写入 Obsidian =========
out="$VAULT/$base.md"
printf '%s\n' "$md" > "$out"
echo "✓ 已写入 $out"
```

---

## 附录 B · `asr_sensevoice.py`（参考实现）

```python
#!/usr/bin/env python3
# 本地 ASR：达摩院 SenseVoice-Small（中文最稳，带标点/ITN）
# 依赖：pip install funasr
# 用法：python3 asr_sensevoice.py <wav路径>   # 转写文本打印到 stdout
import sys
from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

wav = sys.argv[1]

model = AutoModel(
    model="iic/SenseVoiceSmall",
    vad_model="fsmn-vad",
    vad_kwargs={"max_single_segment_time": 30000},
    device="cpu",            # Apple Silicon 用 cpu 最稳；想试 MPS 改 "mps"，不稳再退回
    disable_update=True,
)

res = model.generate(
    input=wav,
    cache={},
    language="zh",           # 中文；混说可改 "auto"
    use_itn=True,
    batch_size_s=60,
    merge_vad=True,
    merge_length_s=15,
)

print(rich_transcription_postprocess(res[0]["text"]))
```
