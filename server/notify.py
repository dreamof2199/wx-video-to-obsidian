#!/usr/bin/env python3
# 钉钉自定义机器人通知（加签方式）。未配置 DING_WEBHOOK 时静默跳过。
# 环境变量：
#   DING_WEBHOOK  钉钉机器人 webhook（https://oapi.dingtalk.com/robot/send?access_token=xxx）
#   DING_SECRET   加签密钥（机器人安全设置选"加签"时给的 SEC 开头的串）；不用加签可留空
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

# 消息统一带这个前缀，便于机器人用"自定义关键词"安全策略（关键词填：视频号笔记）
TAG = "【视频号笔记】"


def verify_inbound(timestamp, sign):
    # 校验钉钉回调签名（用机器人 appSecret）。未配 DING_BOT_SECRET 则跳过校验。
    secret = os.environ.get("DING_BOT_SECRET", "").strip()
    if not secret:
        return True
    if not timestamp or not sign:
        return False
    expect = base64.b64encode(
        hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expect, sign)


def reply(session_webhook, text):
    # 用回调里带的 sessionWebhook 回复消息（短时有效）
    if not session_webhook:
        return
    body = json.dumps({"msgtype": "text", "text": {"content": TAG + text}}).encode()
    try:
        req = urllib.request.Request(session_webhook, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("[notify] 回复失败:", e, flush=True)


def dingtalk(text):
    webhook = os.environ.get("DING_WEBHOOK", "").strip()
    if not webhook:
        return
    url = webhook
    secret = os.environ.get("DING_SECRET", "").strip()
    if secret:
        ts = str(round(time.time() * 1000))
        sign = base64.b64encode(
            hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest())
        url = f"{webhook}&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"
    body = json.dumps({"msgtype": "text", "text": {"content": TAG + text}}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            if d.get("errcode") not in (0, None):
                print("[notify] 钉钉返回:", d, flush=True)
    except Exception as e:
        print("[notify] 钉钉发送失败:", e, flush=True)
