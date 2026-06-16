# wx-video-to-obsidian

> **A manual-trigger pipeline that turns WeChat Channels (视频号) videos into Obsidian notes.** Feed it a Channels share link (or a local `.mp4`) and get back a Markdown note: YAML frontmatter + summary + key points + a cleaned full transcript. Every intermediate step — ASR, OCR, VLM, frame dedup — runs on **local or deterministic inference**; only the final summarization uses a paid AI. Ships in two flavors: a **local macOS build** (zero marginal cost) and a **server build** (Alibaba DashScope + end-to-end-encrypted write into Obsidian LiveSync, triggered over HTTP / DingTalk / WeCom). Docs below are in Chinese.

---

把微信**视频号**视频转成 Obsidian 笔记的手动触发管道。输入一条视频号分享链接（或本地 mp4），输出一则 markdown 笔记：YAML frontmatter + 摘要 + 要点 + 整理后的全文转写。

设计原则：**中间所有节点都是本地推理 / 确定性提取**（ASR、OCR、VLM、抽帧去重），**只有末端做摘要的那一步用付费 AI**。无人声、靠画面轮播的视频也能靠视觉分支兜底。

提供两套实现：

| | 本地版（Mac） | 服务端版（ECS） |
|---|---|---|
| 适用 | Apple Silicon Mac，零边际成本 | 任意端发链接，云端自动出笔记 |
| ASR | 本地 SenseVoice（funasr） | 百炼 Fun-ASR |
| 视觉 | Apple Vision OCR + Qwen3-VL（Ollama） | 百炼 qwen-vl-max |
| 摘要 | `claude -p`（订阅额度） | 百炼 qwen-max |
| 写入 | 直接落 vault 收件夹 | E2E 加密写 CouchDB（LiveSync）|
| 入口 | 命令行 `clip.sh` | HTTP / 钉钉 / 企业微信 |
| 目录 | 仓库根 | [`server/`](server/) |

## 架构

```
源(http直链 或 本地mp4)
  ├─ ffmpeg 抽 16k 音轨 ─────────────────▶ ASR（本地/云，确定性转写）──┐
  └─ ffmpeg 定时抽帧 → mpdecimate 确定性去重 ─▶ OCR + VLM（本地/云）─────┤
                                                                        ▼
                                          摘要 AI（claude -p / qwen-max，唯一付费节点）
                                                                        ▼
                                              markdown 笔记 → Obsidian vault
```

关键确定性约束：抽帧用 `fps=1,mpdecimate` 而**非场景检测**（文字卡背景相似时场景分上不去会漏抽），再在 `visual_extract.py` 内用 difflib 模糊去重。

## 快速开始（本地版）

```bash
brew install ffmpeg jq
pip install funasr torch torchaudio        # 本地 ASR（首跑拉模型数百 MB）
# 视觉：装 Ollama 并 `ollama pull qwen3-vl:30b-a3b`；OCR：pip install ocrmac

cp clip.env.example clip.env               # 配置：至少把 VAULT 改成你的 Obsidian 收件夹
set -a; . ./clip.env; set +a               # 载入配置（也可直接 export 各变量）

clip.sh --doctor                 # 自检依赖 / 下载器 / vault
clip.sh --file <mp4>             # 主路径：下载器拿到的解密 mp4 → 笔记
clip.sh <视频号分享链接>          # 快路径（需先部署解析，见 SETUP.md）
```

配置项全部可用环境变量覆盖（`VAULT` `ASR_BACKEND` `VISUAL_MODE` `VLM_BACKEND` `SAMPLE_FPS` …），`clip.sh --help` 列全。

**取视频的两条路径**和**手把手部署**见 [`SETUP.md`](SETUP.md)：
- 主路径（推荐）：跑下载器 → 微信 PC 客户端播放 → 点注入的「下载」按钮拿解密 mp4。
- 快路径（进阶）：分享链接 → 解析直链；官方实现是 Cloudflare Worker，需自行部署。

## 服务端版

发一条链接即自动出笔记、E2E 加密写入 Obsidian。部署、运维、钉钉/企业微信接入见 [`server/README.md`](server/README.md)。

## 仓库结构

```
clip.sh                  本地版编排入口
clip.env.example         本地版配置模板（复制为 clip.env，source 后运行）
asr_sensevoice.py        本地 ASR（SenseVoice）
asr_dashscope.py         云端 ASR（百炼，备选）
visual_extract.py        抽帧去重 + OCR + VLM
SETUP.md                 本地版手把手部署 / 取视频
server/                  服务端版（FastAPI + 百炼 + LiveSync）
  *.env.example          各配置模板（复制为 *.env 填真实值）
sph-obsidian-pipeline-spec.md  设计规格
```

## 安全与隐私

- **所有密钥都走环境变量 / `*.env` 文件，绝不进 git**；仓库只含 `*.env.example` 模板。
- `.gitignore` 已排除 `*.env`、下载器目录（含 cookie 的 `config.yaml`）、模型缓存等。
- 自己部署前，复制 `*.env.example` → `*.env` 填入你自己的凭据。

## 致谢

视频号解析与下载基于 [`ltaoo/wx_channels_download`](https://github.com/ltaoo/wx_channels_download)。
