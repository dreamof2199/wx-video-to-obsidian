# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目目标

把微信**视频号**视频转成 Obsidian 笔记的手动触发管道。输入是一条视频号分享链接（或一个本地 mp4），输出是 vault 收件夹里一则 markdown（YAML frontmatter + 摘要 + 要点 + 整理后全文转写）。运行在 Apple Silicon Mac，采集+ASR 本地零成本，摘要走 `claude -p` 订阅额度。

权威设计文档是 `sph-obsidian-pipeline-spec.md`（交接规格）。当前 `clip.sh` / `asr_sensevoice.py` 与该文档附录的参考实现一致，**尚未对着真实工具验证**。

## 三条硬约束（勿违反）

1. 采集只用必要的边界动作，**下游全部是确定性 shell/python——不要 agentic 循环、不要 computer-use**。
2. **不要臆测 `parse_sph` 的请求方法与响应字段**——先按下方"三个待验证事实"实测，再改代码。
3. 手动触发、不实时监听——**不要加 fswatch / 守护进程 / 定时轮询 / 去重 / 批量模式**，除非用户明确要求。保持最小。

## 架构

两条采集路径汇入同一条下游管道（`clip.sh` 编排）：

- **主路径（推荐，无需任何"视频地址"）**：跑下载器（注入证书+代理）→ 微信 PC 客户端播放视频号视频 → 点页面注入的「下载」按钮 → 得解密 mp4 → `clip.sh --file <mp4>`。
- **快路径（进阶/可选）**：分享链接 → `POST /api/fetch_video_profile` → 视频直链。**代价高**：官方实现是 Cloudflare Worker，需 `wx_video_download sph_deploy` 部署（Cloudflare 账号 + API Token + `sphCookie`）。多数人用不到。

下游对两条路径无差别：拿到 ffmpeg 可读的源（http 直链或本地 mp4）后，统一走**音频+视觉双分支**（`VISUAL_MODE=both` 默认）：
```
源 ─┬ ffmpeg 抽 16k 音轨 → ASR(本地,确定性转写) ────────────────────────┐
    └ ffmpeg 定时抽帧 fps→mpdecimate 确定性去重 → OCR(Apple Vision)+VLM(本地) ─┤→ claude -p 整合(唯一付费AI) → 笔记
```
**关键设计约束**：中间所有节点都是**本地推理/确定性提取**（ASR/OCR/VLM/去重），**只有末端 claude 是付费 AI**。无人声视频（信息在画面）靠视觉分支兜底；有人声视频音频+画面互相增强。

- `clip.sh` — 编排入口。配置全部可用**环境变量覆盖**：`VAULT` `WXD_DOWNLOAD_DIR` `ASR_BACKEND` `ASR_PYTHON` `VISUAL_MODE` `VLM_BACKEND` `VLM_MODEL` `SAMPLE_FPS` `VISUAL_MAX_FRAMES` 等（`--help` 列全）。支持 `--file`/`--last`/`<链接>`/`--doctor`。
- **键帧抽取**（确定性，无 AI）：`fps=1` 定时抽帧 → `mpdecimate` 丢弃近重复帧（**不用场景检测**——文字卡背景相似时场景分数上不去会漏抽）→ `visual_extract.py` 内再用 difflib 模糊去重（≥0.90 视为同卡）。
- **视觉理解**（`VLM_BACKEND`）：`ollama`（**默认**，模型常驻保温）跑 `qwen3-vl:30b-a3b`；`mlx` 备选（mlx-vlm，模型每次重载）。OCR 用 Apple Vision（`ocrmac`，on-device 中文逐字）。封装 `visual_extract.py`，契约 `python3 visual_extract.py <帧目录>` → 文本到 stdout。
  - 下载提示：HF 直连慢（国内~1.5MB/s），**Ollama 拉 qwen3-vl 快得多（~37MB/s）**，故默认走 ollama。
- **ASR 后端**（`ASR_BACKEND`）：
  - `sensevoice`（**默认**）— 本地 SenseVoice（funasr+torch+torchaudio），离线、零边际成本、无时长顾虑。封装 `asr_sensevoice.py`。**本机已装好并验证**，模型缓存在 `~/.cache/modelscope/`。
  - `dashscope` — 云端百炼 Fun-ASR，封装 `asr_dashscope.py`，凭 `DASHSCOPE_API_KEY`（从环境变量或 `DASHSCOPE_ENV_FILE` 指向的 .env 读，密钥不进本仓库）。无本地模型，可作备选。
  - `mlxwhisper` — 本地备选。
- ASR 脚本契约固定：`python3 asr_xxx.py <音频>` → **纯转写文本**打印到 stdout（funasr 的日志已用 fd 重定向隔离到 stderr，避免污染）。`clip.sh` 依赖此契约。
- 部署：`clip.sh` / `asr_sensevoice.py` / `asr_dashscope.py` / `visual_extract.py` 在 `~/bin/`。**手把手部署/使用见 `SETUP.md`**。
- 依赖踩坑记录：① `python3` 常指向 Xcode/homebrew 的而非装了 funasr/ocrmac 的——clip.sh 用 `pick_python` 自动挑能 import 对应模块的解释器（或 `ASR_PYTHON` 指定）。② Obsidian 标签不能含空格，`fix_tags` 自动把 `AI Agent`→`AI-Agent`。③ 文件名=`日期-标题`（标题去非法字符、限 40 字）。④ claude -p 偶发 403 限流，已加 3 次重试。

## 快路径的真实契约（已对照源码核实，纠正 spec §5 的旧假设）

spec §5 把快路径写成"本地 `GET 127.0.0.1:2022/api/channels/parse_sph?url=` + 仅需 cookie"，**与实现不符**。读 `ltaoo/wx_channels_download` 源码（`cmd/sph.go`、`internal/api/sph/{worker.js,index.html}`）后确认：
1. **不是本地服务**：解析由 `wx_video_download sph_deploy` 部署到用户 Cloudflare 的 **Worker** 提供，地址 `https://<sphWorkerName>.<子域>.workers.dev`。clip.sh 用 `SPH_API` 指向它（默认空＝快路径关闭）；`WXD_API`(2022) 只用于判断本地下载器是否在跑（主路径）。
2. **接口**：`POST $SPH_API/api/fetch_video_profile`，body `{"url":"<分享链接>"}`。
3. **直链字段**（已核实，明文可被 ffmpeg 直读）：`.data.feedInfo.h264VideoInfo.videoUrl`（退 `.h265VideoInfo.videoUrl` → `.videoUrl`）。即 `clip.sh` 的 `extract_src`。
4. **sphCookie**：元宝 web cookie，注入 Worker 环境变量 `COOKIE`；会过期，失效则退主路径 `--file`。

## 常用命令

```bash
brew install ffmpeg jq                       # 系统依赖
pip install funasr torch torchaudio           # 默认本地 ASR（SenseVoice；首跑拉模型数百 MB，本机已装）
pip install dashscope                         # 可选云端 ASR（需配 DASHSCOPE_API_KEY）

clip.sh --doctor                  # 自检：依赖/下载器/ASR/vault
clip.sh --file <mp4>              # 主路径：下载器「下载」按钮拿到的 mp4 → 笔记
clip.sh <视频号分享链接>          # 快路径（进阶，需部署 Cloudflare Worker，见 SETUP.md §C）
python3 asr_sensevoice.py <wav>   # 单独跑本地 ASR

# 快路径真实接口（需先 sph_deploy）：POST /api/fetch_video_profile
curl -s -X POST "$WXD_API/api/fetch_video_profile" -H 'Content-Type: application/json' -d '{"url":"<分享链接>"}' | jq .
```

**完整手把手部署/取视频/用法见 `SETUP.md`**（§A 本地 ASR 安装、§B 用下载器取视频、§C 快路径进阶）。

## 注意

- 错误必须**显式提示**，不要静默吞错——快路径失败时要清楚告知如何走兜底（cookie 失效 vs 私密视频）。
- 密钥一律走环境变量 / `*.env`（已被 `.gitignore` 排除），仓库只含 `*.env.example` 模板；提交前确认无明文凭据。
