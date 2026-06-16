# SETUP · 手把手部署与使用指南

本管道做的事：**一个微信视频号视频 → 一则整理好的 Obsidian 笔记**（标题/摘要/要点/全文转写）。
全程本地：本地下载、**本地语音识别（funasr/SenseVoice）**、Claude 出摘要。除了 Claude，不依赖任何云服务。

**取视频有两条路径，都可用，按场景选：**

| | 主路径（§B） | 快路径（§C） |
|---|---|---|
| 怎么取 | 微信播放 → 点「下载」按钮拿 mp4 → `clip.sh --file` | `clip.sh <分享链接>` 直接解析直链 |
| 前置 | 跑下载器即可，零账号 | 部署 Cloudflare Worker（账号+Token+元宝 cookie） |
| 需要"地址"吗 | 不需要 | 需要分享链接 |
| 适合 | 偶尔几条、最省事 | 想用链接批量/脚本化、不想每条都播放 |
| 直链字段(已核实) | — | `data.feedInfo.h264VideoInfo.videoUrl` |

下游完全一致：拿到 mp4 或直链后 → `ffmpeg 抽音轨 → 本地 ASR → Claude 摘要 → 写 vault`。

- **A. 一次性安装**（含本地 ASR，本机已装好验证）——§A
- **B. 主路径取视频**——§B　　**C. 快路径（Worker）部署**——§C

---

## A. 一次性安装

### A.1 基础工具（命令行依赖）
```bash
brew install ffmpeg jq        # 抽音轨 + 解析 JSON
claude --version              # 确认 Claude Code 可用（出摘要用）
```

### A.2 本地语音识别（funasr / SenseVoice）—— 本地 ASR 部署方案
这是你要的"本地部署 ASR"。**本机已经装好并验证过**，下面是它做了什么、以及换机器怎么重装。

1. **装 Python 依赖**（约 1.5GB，含 PyTorch）：
   ```bash
   pip install funasr torch torchaudio
   ```
   - 用的是系统 `python3`（如 conda/homebrew 的 `python3`）。换 Python 环境要在同一个环境里装。

2. **首次运行自动下载模型**（约几百 MB，之后永久走本地缓存，离线可用）：
   - 识别模型 `iic/SenseVoiceSmall`、断句模型 `fsmn-vad`
   - 缓存位置：`~/.cache/modelscope/hub/models/iic/`（本机已存在，无需再下）

3. **自检本地 ASR 是否就绪**：
   ```bash
   # 造一段测试语音（macOS 自带 say）→ 转 wav → 本地识别
   say -v Tingting "测试本地语音识别是否正常" -o /tmp/t.aiff
   ffmpeg -y -i /tmp/t.aiff -ar 16000 -ac 1 /tmp/t.wav
   ASR_BACKEND=sensevoice python3 ~/bin/asr_sensevoice.py /tmp/t.wav
   # 应只打印一行转写文本（模型日志走 stderr，不会污染结果）
   ```

4. **设备说明**：默认 `cpu`（Apple Silicon 最稳）。想试 GPU 加速：`export ASR_DEVICE=mps`（不稳就删掉这行退回 cpu）。
   中英混说：`export ASR_LANGUAGE=auto`（默认 `zh`）。

> 备选本地引擎（可不管）：`pip install mlx-whisper` 后 `export ASR_BACKEND=mlxwhisper`，Apple Silicon 原生。

### A.2b 视觉理解（OCR + 本地 VLM）—— 处理"信息在画面/无人声"的视频
有些视频没有有效人声（只有背景音乐），关键信息以**文字卡/图表轮播**呈现。这条分支把画面信息也提进笔记。
默认 `VISUAL_MODE=both`：每条视频同时跑音频(ASR)和视觉，由 claude 合并；无人声时纯靠视觉。**全本地、不付费**。

1. **OCR（Apple Vision，逐字提取）**：`pip install ocrmac`（包很小，用 macOS 自带 Vision，中文准、on-device）。
2. **本地 VLM（看懂图表/版式）**：用 Ollama 跑 Qwen3-VL（**比 HF 下载快得多**）：
   ```bash
   ollama pull qwen3-vl:30b-a3b    # ~19GB，约 37MB/s；64GB 内存够跑
   ```
   备选轻量：`qwen3-vl:8b`（~6GB，更快）。Ollama 会让模型常驻保温，连跑多条不重载。
3. **键帧抽取是确定性的**（不是 AI）：定时抽帧 `fps` + `mpdecimate` 去重，每张卡留一帧。不用场景检测（文字卡背景相似会漏抽）。
4. **自检**：`clip.sh --doctor` 的"视觉理解"一节应显示 `✓ ocrmac` 和 `✓ VLM(ollama) qwen3-vl:30b-a3b 就绪`。
5. **只想要音频**：`export VISUAL_MODE=audio`；**只想要画面**：`VISUAL_MODE=visual`。

> 约束：除最终 claude 摘要外，全流程不调用任何**付费** AI——OCR/ASR/VLM/去重都是本地推理或确定性算法。

### A.3 让"本地 ASR + 你的笔记库"成为默认
把下面几行加到 `~/.zshrc`（一次性，之后每个终端都生效）：
```bash
export ASR_BACKEND=sensevoice                          # 用本地 ASR
export VAULT="$HOME/你的Obsidian库/收件夹"             # ←改成你真实的 Obsidian 文件夹绝对路径
```
改完执行 `source ~/.zshrc`。

### A.4 总检查
```bash
clip.sh --doctor
```
本地 ASR 这条看到 `✓ funasr`、`✓ ~/bin/asr_sensevoice.py` 即就绪。（下载器那条 ✗ 正常——只在 §B 实际取视频时才需要它在跑。）

---

## B. 每次用：把一个视频号视频变成笔记

主流程**不需要复制任何链接/地址**。核心是用下载器在微信里点一下「下载」拿到 mp4。

### B.1 装下载器（只第一次）
1. 去 [wx_channels_download Releases](https://github.com/ltaoo/wx_channels_download/releases) 下载 **macOS / Apple Silicon** 那个包，解压得到一个可执行文件（这里叫 `wx_video_download`）。
2. 终端进到它所在目录，首次以管理员运行：
   ```bash
   chmod +x ./wx_video_download
   sudo ./wx_video_download
   ```
   - 第一次会**自动安装证书 + 开启系统代理**，看到终端打印 **`代理服务启动成功`** 就绪。
   - 若系统拦截"未签名开发者"：系统设置 → 隐私与安全性 → 点"仍要打开"，再重跑。
   - 之后可双击打开，不必每次 sudo。

### B.2 下载路径（在 config.yaml 改）
下载器把 mp4 存到 `config.yaml` 的 `download.dir`：
```yaml
download:
  defaultHighest: false
  filenameTemplate: "{{filename}}_{{spec}}"   # 文件名形如 标题_清晰度.mp4
  dir: "%UserDownloads%"                        # ←改这里。%UserDownloads% = ~/Downloads
```
- 想改到固定目录，填**绝对路径**，例如 `dir: "$HOME/VideoInbox"`。
- 改完让 `clip.sh --last` 也指向它：`export WXD_DOWNLOAD_DIR="$HOME/VideoInbox"`（写进 `~/.zshrc`）。
- 另外 `api.port: 2022`、`proxy.port: 2023` 是下载器自己的端口，别和别的服务撞。

### B.3 下载视频（每个视频）
1. **保持下载器开着**（终端别关）。
2. 打开**微信 PC 客户端**，找到视频号视频，**点开播放**。
3. 下载器会在页面里**注入「下载」按钮**——通常在视频下方操作栏；没看到就留意页面侧边/底部悬浮按钮。
4. 点它 → 自动下载并**解密成 mp4**，落到上面的 `download.dir`。

### B.4 变成笔记（一条命令）
```bash
clip.sh --last                       # 自动取 WXD_DOWNLOAD_DIR 里最新的 mp4（推荐）
clip.sh --file <某个具体 mp4 路径>    # 或指定文件
```
抽音轨 → **本地 ASR** → Claude 整理 → 写进 `VAULT`，打印 `✓ 已写入 ...`。

> 用完下载器 **Ctrl+C 退出**（别直接关窗口），否则残留系统代理会导致上网异常。
> 清理：系统设置 → 网络 → 代理，确认 HTTP/HTTPS 代理已关闭。

### ⚠️ B.5 启动下载器后视频号白屏/「正在加载」打不开 —— 排查
下载器靠**系统代理(127.0.0.1:2023) + 根证书**拦截 HTTPS 来抓流。微信内置浏览器没走通这条就会白屏。
关掉下载器(Ctrl+C)后视频恢复正常＝就是这个原因。按顺序排查（依据项目 issue #303/#420/#422）：

1. **关掉一切 VPN/翻墙/代理类与安全软件**：Clash、Surge、Shadowrocket、ClashX、Norton 等会和下载器的系统代理打架——这是最常见根因（#303 是 Norton，#422 是防火墙）。退出它们后**完全退出微信再重开**。
   - **不想关 VPN？** 见下方「B.6 VPN 用户」。视频号是国内服务，被 VPN 绕到海外出口本身就会白屏/超慢，让它直连才是正解。
2. **确认根证书被信任**：钥匙串访问 → 搜 `SunnyRoot`/`Sunny` → 双击 → 展开"信任" → SSL/「使用此证书时」设为**始终信任**。改完**重启电脑**让信任生效。
3. **命令行验证代理+证书是否通**（下载器运行时）：
   ```bash
   curl -x http://127.0.0.1:2023 https://channels.weixin.qq.com -I
   ```
   - 返回 `HTTP/2 200` 且无 SSL 报错 → 代理与证书 OK，问题在微信 webview 没认证书 → 做第 2、4 步。
   - SSL 报错 → 证书没装/没信任 → 做第 4 步。
4. **重装证书**：`./wx_video_download uninstall` → 重启 → 再 `sudo ./wx_video_download` 让它重装。
5. 仍不行（少数环境，微信 WKWebView 不读系统钥匙串）：按官方 [自定义 CA 证书](https://ltaoo.github.io/wx_channels_download/config/cert.html) 自己生成并配置；或直接改用 **§C 快路径**——它走 Cloudflare Worker 解析，**完全不需要本地代理和证书，不会白屏**。

### B.6 VPN 用户（不想关 VPN 也能用）
全局 VPN（如 **Astrill StealthVPN**、Clash TUN、各类商业 VPN）把所有流量绕到海外出口，
视频号（国内服务）被绕走就会白屏/超慢。三选一：

- **① 分流让视频号直连（推荐，VPN 照常开）**：
  - **Astrill（StealthVPN）**：⚠️ 关键——**StealthVPN 下 Site Filter 按 IP 匹配，不认域名**，填 `*.qq.com` 无效！
    正确做法：Site Filter → 把**模式**改成 **`Tunnel only international sites`**（只隧道国际网站／智能模式）——
    它自动让国内站点（视频号/qq.com）直连、只把国际流量走 VPN。设好后**完全退出微信重开**。
    （想用域名过滤得把协议换成 **OpenWeb**；macOS 因 Apple 沙盒没有应用级 Application Filter。）
  - **Clash/Surge**：加规则 `DOMAIN-SUFFIX,qq.com,DIRECT`（官方还提供把 qq.com 转发给下载器的 Script，见 [proxy 文档](https://ltaoo.github.io/wx_channels_download/config/proxy.html)）。
- **② 用 §C 快路径（最省心，VPN 全程开着不冲突）**：快路径走 Cloudflare Worker 在云端解析，
  不碰本地系统代理和证书，VPN 开着也不白屏。代价是先部署一次 Worker。
- **③ 下载那一下临时关 VPN**：最简单，但你不想关就用 ① 或 ②。

> 别开下载器的 `tun: true`：会和 VPN 抢系统路由表，更乱（官方明确不建议同时开）。

---

## C. 快路径：部署 Cloudflare Worker，用分享链接直接解析

跳过"播放+点下载"，直接 `clip.sh <视频号分享链接>` 拿直链入管道。适合用链接批量/脚本化。
原理：`wx_video_download sph_deploy` 把一个解析用的 Worker 部署到你的 Cloudflare，
Worker 用元宝 cookie 调微信 finder 接口，返回视频信息（含明文直链）。**契约已对照源码核实**。

### C.1 准备 Cloudflare 凭据（一次性）
1. 注册/登录 [Cloudflare](https://dash.cloudflare.com)。
2. **Account ID**：进 Workers & Pages 概览，右侧栏即有 `Account ID`，复制。
3. **API Token**：右上头像 → My Profile → API Tokens → Create Token →
   用 **"Edit Cloudflare Workers"** 模板（含 Workers Scripts:Edit 权限）→ 生成并复制（只显示一次）。

### C.2 准备元宝 cookie（sphCookie）
1. 浏览器登录 [yuanbao.tencent.com](https://yuanbao.tencent.com)。
2. F12 → Network → 刷新 → 点任一文档请求 → Request Headers 里整段复制 `Cookie:` 的值。
   （这串用于 Worker 调视频号接口的身份认证；**会过期**，失效就重取重部署。）

### C.3 配置并部署
你现有的 `config.yaml` 里 `cloudflare:` 块是给 **mp-rss（公众号RSS）** 用的（`workerName: mp-rss-api`、`d1Name` 等）。
视频号解析要在**同一个块里补 4 个键**（`accountId`/`apiToken` 填上，`sphWorkerName`/`sphCookie` 新增），原有键保留：
```yaml
cloudflare:
  accountId: "你的 Account ID"        # ←填上（原来是空的）
  apiToken: "你的 API Token"          # ←填上（原来是空的）
  refreshToken: "wx_channels_download" # 原有，mp-rss 用，保留
  adminToken: ""                       # 原有，保留
  workerName: "mp-rss-api"             # 原有，mp-rss 的 worker，保留
  d1Name: "mp-rss-db"                  # 原有，保留
  sphWorkerName: "wx-sph"              # ←新增：视频号 worker 名，自取，会成子域名
  sphCookie: "上面复制的元宝 cookie 整段"  # ←新增
```
部署（只部署视频号 worker，不影响 mp-rss）：
```bash
wx_video_download sph_deploy
```
成功后终端打印 Worker 地址，形如 `https://wx-sph.<你的子域>.workers.dev`。

### C.4 接到 clip.sh
```bash
export SPH_API="https://wx-sph.<你的子域>.workers.dev"   # ←上一步打印的地址；建议写进 ~/.zshrc
clip.sh --doctor          # "快路径：Worker ... 可达" 应为 ✓
```

### C.5 验证契约（可选，确认返回结构）
```bash
curl -s -X POST "$SPH_API/api/fetch_video_profile" \
  -H 'Content-Type: application/json' -d '{"url":"<视频号分享链接>"}' \
  | jq '.data.feedInfo | {h264:.h264VideoInfo.videoUrl, h265:.h265VideoInfo.videoUrl, bare:.videoUrl, desc:.description}'
```
`clip.sh` 取直链的字段就是 `data.feedInfo.h264VideoInfo.videoUrl`（退 h265 → 裸 `videoUrl`）。
若哪天官方改了字段，改 `clip.sh` 里 `extract_src` 那段 jq 即可。

### C.6 怎么拿分享链接
微信里打开该视频号视频 → 右下角**分享/转发**箭头 → **复制链接**，得到 `https://weixin.qq.com/sph/xxxx`。
```bash
clip.sh "https://weixin.qq.com/sph/xxxx"
```
走快路径时笔记的 `source:` 会自动回填成这条链接。`sphCookie` 失效则报错并提示退 §B 的 `--file`。

---

## 日常速查
```bash
clip.sh --last           # 主路径：取下载目录最新 mp4 → 笔记（最省事，§B）
clip.sh --file <mp4>     # 主路径：指定 mp4 → 笔记
clip.sh <分享链接>        # 快路径：链接直接解析 → 笔记（§C，需 SPH_API）
clip.sh --doctor         # 自检
```
脚本在 `~/bin/`：`clip.sh`、`asr_sensevoice.py`（本地，默认）、`asr_dashscope.py`（云端可选）。
环境变量：`VAULT ASR_BACKEND ASR_LANGUAGE ASR_DEVICE`（ASR）｜`SPH_API WXD_API PARSE_PATH`（快路径）。

## 常见问题
- **没看到「下载」按钮**（主路径）：确认下载器在运行且打印过"代理服务启动成功"；在**微信 PC 客户端**操作；视频**点开播放**后再找（视频下方或页面悬浮）。
- **快路径报"未拿到直链"**：多半 `sphCookie` 过期——重取重 `sph_deploy`；或确认 `SPH_API` 地址正确、私密视频无法解析。
- **转写为空/"音频可能无人声"**：`ffmpeg -i 文件.mp4` 看有没有 Audio 流。
- **首次跑很慢**：本地 ASR 第一次下模型；之后就快了。超长视频本地识别耗时随时长增加属正常。
- **笔记 source 是"占位"**：`--file` 没有原始链接故留占位；走 §C 快路径才会回填真实链接。
