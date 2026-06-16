#!/usr/bin/env python3
# clip-server：视频号链接 → 笔记 → 写入 Obsidian LiveSync vault
# POST /clip {"url": "...", "folder": "wx_video"(可选), "token": "..."} → 入队，立即返回受理
# GET  /health  GET /queue
# 单 worker 串行处理（内存受限的小机器：一次只跑一个 ffmpeg，保护同机生产服务）
import os
import queue
import re
import threading
import traceback
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import pipeline
import livesync
import notify
import wecom

VAULT_FOLDER = os.environ.get("VAULT_FOLDER", "wx_video")
API_TOKEN = os.environ.get("CLIP_API_TOKEN", "")

app = FastAPI(title="clip-server")
_q: "queue.Queue[tuple]" = queue.Queue()
_stats = {"queued": 0, "done": 0, "failed": 0, "last": ""}


class ClipReq(BaseModel):
    url: str
    folder: Optional[str] = None
    token: Optional[str] = None


def _worker():
    while True:
        url, folder, wx_user = _q.get()
        try:
            name, md = pipeline.process(url)
            path = f"{folder}/{name}.md"
            msg = livesync.write_note(path, md)
            _stats["done"] += 1
            _stats["last"] = f"OK {path}"
            print(f"[clip] ✓ {path}: {msg.splitlines()[0][:80]}", flush=True)
            notify.dingtalk(f"✅ 已生成笔记\n{path}\n{url}")
            wecom.send_text(wx_user, f"✅ 已生成笔记\n{path}")
        except Exception as e:
            _stats["failed"] += 1
            _stats["last"] = f"FAIL {url}: {e}"
            print(f"[clip] ✗ {url}: {e}\n{traceback.format_exc()}", flush=True)
            notify.dingtalk(f"❌ 处理失败\n{url}\n原因：{e}")
            wecom.send_text(wx_user, f"❌ 处理失败：{e}")
        finally:
            _q.task_done()


threading.Thread(target=_worker, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/queue")
def qstatus():
    return {"pending": _q.qsize(), **_stats}


@app.post("/clip")
def clip(req: ClipReq):
    if API_TOKEN and req.token != API_TOKEN:
        return {"ok": False, "error": "unauthorized"}
    if not req.url.startswith("http"):
        return {"ok": False, "error": "url 非法"}
    folder = req.folder or VAULT_FOLDER
    _q.put((req.url, folder, None))
    _stats["queued"] += 1
    return {"ok": True, "accepted": req.url, "folder": folder, "pending": _q.qsize()}


# 钉钉入站：群里 @机器人 发视频号链接 → 自动生成笔记
LINK_RE = re.compile(r"https?://(?:weixin\.qq\.com/sph/\w+|channels\.weixin\.qq\.com/\S+)")


@app.post("/ding/callback")
async def ding_callback(request: Request):
    if not notify.verify_inbound(request.headers.get("timestamp"), request.headers.get("sign")):
        return {"errcode": 1, "errmsg": "bad sign"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or {}).get("content", "").strip()
    session = body.get("sessionWebhook", "")
    m = LINK_RE.search(text)
    if not m:
        notify.reply(session, "没识别到视频号链接，请把分享链接（https://weixin.qq.com/sph/...）作为文字发给我")
        return {}
    url = m.group(0)
    _q.put((url, VAULT_FOLDER, None))
    _stats["queued"] += 1
    notify.reply(session, f"已收到，正在生成笔记…\n{url}")
    return {}


# 企业微信自建应用回调：URL 验证(GET) + 收消息(POST)
@app.get("/wx/callback")
def wx_verify(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    if not wecom.configured():
        return PlainTextResponse("wecom not configured", status_code=503)
    plain = wecom.verify_url(msg_signature, timestamp, nonce, echostr)
    return PlainTextResponse(plain or "", status_code=200 if plain else 403)


@app.post("/wx/callback")
async def wx_callback(request: Request, msg_signature: str = "", timestamp: str = "", nonce: str = ""):
    if not wecom.configured():
        return PlainTextResponse("", status_code=503)
    body = (await request.body()).decode("utf-8", "replace")
    try:
        msg = wecom.parse_message(msg_signature, timestamp, nonce, body)
    except Exception as e:
        print("[wx] 解析失败:", e, flush=True)
        return PlainTextResponse("", status_code=403)
    if not msg:
        return PlainTextResponse("", status_code=403)
    user = msg.get("from")
    text = msg.get("content", "")
    m = LINK_RE.search(text)
    if not m:
        wecom.send_text(user, "没识别到视频号链接，请把分享链接（https://weixin.qq.com/sph/...）作为文字发给我")
        return PlainTextResponse("")
    _q.put((m.group(0), VAULT_FOLDER, user))
    _stats["queued"] += 1
    wecom.send_text(user, f"已收到，正在生成笔记…\n{m.group(0)}")
    return PlainTextResponse("")
