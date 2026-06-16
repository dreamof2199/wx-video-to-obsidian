# clip-server 运维手册

视频号分享链接 → 自动生成笔记 → E2E 加密写入 Obsidian（LiveSync CouchDB）。
部署在阿里云 ECS，与现有微信小程序后端共存。

## 1. 架构与位置

```
手机/电脑 ──POST https://your-domain.com/clip (带 token)──▶ nginx ──▶ clip-server :8010
   clip-server 内部：
   ├ 解析  parse_sph.py   本地直连 yuanbao+channels（不依赖 Cloudflare）
   ├ 下载  curl -4        拿 mp4 到临时目录（绕开 ffmpeg IPv6 问题）
   ├ 音频  ffmpeg+DashScope Fun-ASR
   ├ 画面  ffmpeg 抽帧(mpdecimate去重) + DashScope qwen-vl-max（并发，OCR+理解）
   ├ 摘要  DashScope qwen-max → markdown（清洗/限长/tag修复）
   └ 写入  → obsidian-sync-mcp :8787 ──E2E加密──▶ CouchDB ──▶ Obsidian 各端
   完成/失败 → 钉钉机器人通知（可选）
```

| 项 | 值 |
|---|---|
| ECS | <REGION> / `<INSTANCE_ID>` / <ECS_PUBLIC_IP> / CentOS Stream 8 |
| 代码目录 | `/root/clip-server/` |
| 服务1 | `clip-server.service` → uvicorn `clip_server:app` 127.0.0.1:**8010** |
| 服务2 | `obsidian-sync-mcp.service` → node，127.0.0.1:**8787**（LiveSync 写入后端） |
| 入口 | nginx `location /clip` `/queue` 在 `/etc/nginx/nginx.conf` 的 your-domain.com:443 块 |
| 触发 token | 在 `/root/clip-server/clip.env` 的 `CLIP_API_TOKEN` |
| 系统依赖 | python3.11、ffmpeg(静态 /usr/local/bin)、node20(/usr/local/node-*) |
| 内存保护 | 2G swap + 服务 MemoryMax（clip 800M / mcp 350M）+ 串行队列 |

## 2. 日常使用

```bash
# 触发（folder 可选，默认 wx_video）
curl -X POST https://your-domain.com/clip -H 'Content-Type: application/json' \
  -d '{"url":"<视频号分享链接>","token":"<TOKEN>"}'

# 看状态
curl https://your-domain.com/queue
# {"pending":待处理, "queued":累计入队, "done":成功, "failed":失败, "last":"最近一条结果"}
```
> iOS 快捷指令：「共享表」接收链接 → 「获取 URL 内容」POST 上面的接口。串行处理，多条会排队。

## 3. 运维命令（ECS 上）

```bash
# 状态
systemctl status clip-server obsidian-sync-mcp
# 日志（实时）
journalctl -u clip-server -f
journalctl -u obsidian-sync-mcp -n 50 --no-pager
# 重启
systemctl restart clip-server
# 内存
free -h
```

## 4. 元宝 cookie 过期处理（最常见维护）

解析靠元宝 web cookie（`sphCookie`），**会过期**。失效表现：`/queue` 的 `failed` 增加，
`last` 显示「未拿到 wx_export_id（sphCookie 失效）」，钉钉收到失败通知。

更新步骤：
1. 浏览器登录 yuanbao.tencent.com → F12 → Network → 刷新 → 任一请求 → 复制整段 `Cookie:` 值。
2. 在 ECS 上更新（注意整段一行、不要换行）：
   ```bash
   printf 'SPH_COOKIE=%s\n' '<粘贴的完整cookie>' > /root/clip-server/sph_cookie.env
   chmod 600 /root/clip-server/sph_cookie.env
   systemctl restart clip-server
   ```
3. 发一条链接验证 `/queue` 的 `done` 是否+1。

## 5. 钉钉通知（可选，强烈建议开）

服务已内置，配上 webhook 即生效（每条成功/失败都推送）。**创建机器人见 §7**。
配置：
```bash
# 把这两行加进 /root/clip-server/clip.env（DING_SECRET 仅"加签"方式需要）
echo 'DING_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxxxx' >> /root/clip-server/clip.env
echo 'DING_SECRET=SECxxxxxxxx' >> /root/clip-server/clip.env
systemctl restart clip-server
```

## 6. 密钥与配置文件

仓库只含 `*.env.example` 模板，首次部署逐个复制为 `*.env` 填真实值（`chmod 600`）：

```bash
cd /root/clip-server
for f in clip osmcp sph_cookie ding wx; do cp $f.env.example $f.env; done
chmod 600 *.env
vim clip.env osmcp.env sph_cookie.env   # 必填；ding.env / wx.env 按需
```

| 文件（模板） | 内容 | 必填 | 加载方 |
|---|---|---|---|
| `clip.env` | DASHSCOPE_API_KEY、CLIP_API_TOKEN、模型、抽帧调参、SPH_API | ✅ | clip-server.service |
| `sph_cookie.env` | SPH_COOKIE（元宝 cookie，本地解析快路径用） | 用快路径则填 | clip-server.service |
| `osmcp.env` | CouchDB 账号密码 + **E2E passphrase** + VAULT_NAME | ✅ | obsidian-sync-mcp.service |
| `ding.env` | 钉钉出站/入站凭据 | 可选 | clip-server.service |
| `wx.env` | 企业微信自建应用凭据 | 可选 | clip-server.service |

代码里无任何明文密钥。改任何 env 后都要 `systemctl restart` 对应服务。

## 7. 怎么建钉钉机器人 + webhook

**钉钉自定义机器人**（群机器人，最简单），步骤：
1. 手机/电脑钉钉里**新建一个群**（或用已有群，比如建个「视频号笔记」群，只有你自己也行）。
2. 群右上角 **设置（…）→ 智能群助手 → 添加机器人 → 自定义**（带"Webhook"字样那个）。
3. 头像名字随意 → **安全设置**三选一（建议**加签**，最安全）：
   - **加签**：勾选后会给你一串 `SEC` 开头的密钥 → 这就是 `DING_SECRET`。
   - 自定义关键词：填 `视频号笔记`（本服务消息都带这个前缀，能通过）。本服务也支持，但加签更稳。
   - IP 段：填 ECS 公网 IP `<ECS_PUBLIC_IP>`。
4. 勾「我已阅读并同意」→ 完成 → 复制 **Webhook 地址**（形如 `https://oapi.dingtalk.com/robot/send?access_token=xxxx`）→ 这就是 `DING_WEBHOOK`。
5. 把 `DING_WEBHOOK`（和加签的 `DING_SECRET`）按 §5 填进 `clip.env`、重启。

发消息原理：服务 POST 这个 webhook，body `{"msgtype":"text","text":{"content":"..."}}`；
加签时再带 `&timestamp=..&sign=..`（HMAC-SHA256），`notify.py` 已实现。

### 7b. 入站机器人（你发链接 → 自动生成笔记）

⚠️ **自定义群机器人只能出站**。要"接收消息"必须用**钉钉开放平台·企业内部机器人**（HTTP 回调）。
服务端已就绪：接口 `POST /ding/callback`（提取视频号链接→入队→回复"已收到"），nginx 入口 `https://your-domain.com/ding/callback` 已配。

创建步骤：
1. 浏览器开 [open-dev.dingtalk.com](https://open-dev.dingtalk.com) → **应用开发 → 创建应用**（企业内部应用）。
2. 进应用 → **添加应用能力 → 机器人** → 配置名字头像。
3. **消息接收模式选「HTTP」**，**消息接收地址**填：`https://your-domain.com/ding/callback`。
4. 保存并**发布**。
5. 在「应用凭证」里拿到 **AppSecret** → 这就是 `DING_BOT_SECRET`（用于校验回调签名）。
6. 把机器人**加进一个群**（群设置→机器人→添加自建机器人）。
7. 配置 ECS：
   ```bash
   echo 'DING_BOT_SECRET=<应用 AppSecret>' >> /root/clip-server/clip.env
   systemctl restart clip-server
   ```
8. 群里 **@机器人** 发一条视频号分享链接（作为文字）→ 机器人回「已收到」→ 笔记自动进 Obsidian。

> 安全：设 `DING_BOT_SECRET` 后才校验签名；不设则 `/ding/callback` 接受任意请求（有人乱发会刷 DashScope 费用），**务必配上**。
> 注意：分享的视频号若是「卡片」形式回调里可能取不到链接 → 让发送方**复制链接作为文字**发。

### 7c. 企业微信自建应用（已上线）

在企业微信里给应用发视频号链接 → 自动生成笔记 + 回复。已部署：`POST /wx/callback`（验签/AES解密/收消息）+ 主动回复（`wecom.py`）。

配置在 `/root/clip-server/wx.env`（已配好）：
```
WX_CORPID=企业ID  WX_SECRET=应用Secret  WX_AGENTID=应用AgentId
WX_TOKEN=接收消息Token  WX_AESKEY=EncodingAESKey(43位)
```
后台要点：
- **接收消息**回调地址 = `https://your-domain.com/wx/callback`，Token/AESKey 必须和 wx.env 一致。
- **企业可信IP** 要含 ECS 公网 `<ECS_PUBLIC_IP>`，否则主动回复发不出（errcode 60020）。
- 改 Token/AESKey/Secret 后 `systemctl restart clip-server`，再回后台「保存」让它重新验证。
- 收发都靠这一个应用；DingTalk 那套与它并存，互不影响。

## 8. 更新代码（重新部署）

本机改完 `server/*.py` 后，用 aliyun-ecs-deploy 推送：
```bash
cd ~/.claude/skills/aliyun-ecs-deploy
export ECS_REGION=<REGION> ECS_INSTANCE=<INSTANCE_ID>
./scripts/push_files.sh /root/clip-server ~/wx-tech/wx-video-scratch/server/pipeline.py   # 改了哪个传哪个
./scripts/ecs_run.sh 'cd /root/clip-server; [ -d Users ] && { mv Users/jin/*/*/*/server/*.py .; rm -rf Users; }; systemctl restart clip-server' 30
```
（push_files 会带绝对路径，需 mv 回根目录——已知行为。）

## 9. 排障速查

| 现象 | 处理 |
|---|---|
| `/queue` failed 增加，last 含「sphCookie 失效」 | 元宝 cookie 过期 → §4 更新 |
| last 含「视频下载失败」 | CDN 直链过期（解析到下载间隔太久）或网络 → 重发即可；或看 `journalctl` |
| last 含「音频和画面都没提取到内容」 | 视频无音轨且无可识别画面，或 ffmpeg 失败 → 看日志 |
| 写入无反应、Obsidian 没出现 | `systemctl status obsidian-sync-mcp`；`journalctl -u obsidian-sync-mcp`；确认 CouchDB 可达 |
| 内存紧张 | `free -h` 看 swap；服务有 MemoryMax 上限不会拖垮生产；必要时降低 `VL_CONCURRENCY`（clip.env，默认6） |
| 误改 nginx | 备份在 `/etc/nginx/nginx.conf.bak.cliptr`；`nginx -t` 校验，`cp` 回滚后 `nginx -s reload` |

## 10. 可调参数（clip.env，改完重启）

- `VISUAL_MODE` = both(默认) / audio / visual
- `VL_CONCURRENCY` = 并发调 qwen-vl 的帧数（默认6；内存紧可调小）
- `VISUAL_MAX_FRAMES` = 关键帧上限（默认60）
- `SAMPLE_FPS` / `MPDECIMATE` = 抽帧帧率/去重灵敏度
- `DASHSCOPE_MODEL_VL` / `DASHSCOPE_MODEL_TEXT` = 模型（默认 qwen-vl-max / qwen-max）
- `VAULT_FOLDER` = 默认写入文件夹（默认 wx_video）
