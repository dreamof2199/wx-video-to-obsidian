#!/usr/bin/env python3
# 企业微信自建应用：回调消息 AES 解密/URL验证 + 主动发消息。
# 环境变量：WX_CORPID WX_SECRET WX_TOKEN WX_AESKEY(43位EncodingAESKey) WX_AGENTID
import base64
import hashlib
import json
import os
import struct
import time
import urllib.request
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES


def configured():
    return bool(os.environ.get("WX_CORPID") and os.environ.get("WX_TOKEN") and os.environ.get("WX_AESKEY"))


def _aeskey():
    return base64.b64decode(os.environ["WX_AESKEY"] + "=")


def _sign(ts, nonce, encrypt):
    token = os.environ["WX_TOKEN"]
    return hashlib.sha1("".join(sorted([token, ts, nonce, encrypt])).encode()).hexdigest()


def _decrypt(encrypt_b64):
    key = _aeskey()
    cipher = AES.new(key, AES.MODE_CBC, key[:16])
    plain = cipher.decrypt(base64.b64decode(encrypt_b64))
    plain = plain[:-plain[-1]]                       # 去 PKCS7 padding
    msg_len = struct.unpack(">I", plain[16:20])[0]   # random(16)+len(4)+msg+corpid
    return plain[20:20 + msg_len].decode("utf-8")


def verify_url(msg_sig, ts, nonce, echostr):
    if _sign(ts, nonce, echostr) != msg_sig:
        return None
    return _decrypt(echostr)


def parse_message(msg_sig, ts, nonce, body):
    encrypt = ET.fromstring(body).find("Encrypt").text
    if _sign(ts, nonce, encrypt) != msg_sig:
        return None
    root = ET.fromstring(_decrypt(encrypt))
    return {"from": root.findtext("FromUserName"),
            "type": root.findtext("MsgType"),
            "content": (root.findtext("Content") or "").strip()}


_tok = {"v": "", "exp": 0.0}


def _get_token():
    if _tok["v"] and time.time() < _tok["exp"]:
        return _tok["v"]
    url = ("https://qyapi.weixin.qq.com/cgi-bin/gettoken"
           f"?corpid={os.environ['WX_CORPID']}&corpsecret={os.environ['WX_SECRET']}")
    d = json.loads(urllib.request.urlopen(url, timeout=10).read())
    _tok["v"] = d["access_token"]
    _tok["exp"] = time.time() + d.get("expires_in", 7200) - 200
    return _tok["v"]


def send_text(touser, text):
    if not configured() or not touser:
        return
    if not os.environ.get("WX_AGENTID") or not os.environ.get("WX_SECRET"):
        print("[wecom] 未配 WX_AGENTID/WX_SECRET，跳过主动发送", flush=True)
        return
    try:
        body = json.dumps({"touser": touser, "msgtype": "text",
                           "agentid": int(os.environ["WX_AGENTID"]),
                           "text": {"content": text}}).encode()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={_get_token()}"
        urllib.request.urlopen(urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as e:
        print("[wecom] 发送失败:", e, flush=True)
