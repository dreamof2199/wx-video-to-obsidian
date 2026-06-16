#!/usr/bin/env python3
# 视频号分享链接 → 视频信息（含明文直链）。
# 把 ltaoo/wx_channels_download 的 Cloudflare Worker(internal/api/sph/worker.js) 移植到本地 Python：
#   Step1 POST yuanbao.tencent.com 解析分享链接 → export_id + playable_url（需 sphCookie）
#   Step2 POST channels.weixin.qq.com 取 feed info → data.feedInfo.h264VideoInfo.videoUrl
# 国内服务端直连两个腾讯接口，无需 Cloudflare（workers.dev 在国内常不可达）。
import json
import os
import random
import time
import urllib.parse
import urllib.request

PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_INFO_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"

PARSE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": "https://yuanbao.tencent.com",
    "referer": "https://yuanbao.tencent.com/chat/naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "t-userid": "b9575f6b0a8c4a55a08096904a5ef20a",
    "x-agentid": "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "x-commit-tag": "72282a0d",
    "x-device-id": "1921b001708100d7fa31002b9646bd0cc15a3e2e1f",
    "x-hy106": "",
    "x-hy92": "e963067ffa31002b9646bd0c03000008b1951a",
    "x-hy93": "1921b001708100d7fa31002b9646bd0cc15a3e2e1f",
    "x-id": "b9575f6b0a8c4a55a08096904a5ef20a",
    "x-instance-id": "5",
    "x-language": "zh-CN",
    "x-os_version": "Mac OS(10.15.7)-Blink",
    "x-platform": "mac",
    "x-requested-with": "XMLHttpRequest",
    "x-source": "web",
    "x-web-third-source": "main",
    "x-webdriver": "0",
    "x-webversion": "2.69.0",
    "x-ybuitest": "0",
}

FEED_INFO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": "https://channels.weixin.qq.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


def _post(url, headers, payload, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _generate_rid():
    ts = format(int(time.time()), "x")
    rnd = "".join(random.choice("0123456789abcdef") for _ in range(8))
    return f"{ts}-{rnd}"


def parse_share_url(share_url, cookie):
    headers = dict(PARSE_HEADERS, cookie=cookie)
    result = _post(PARSE_URL, headers, {"type": "video_channel_url", "url": share_url, "scene": 1})
    data = result.get("data")
    if not data or not data.get("wx_export_id"):
        raise RuntimeError("parseShareUrl: 未拿到 wx_export_id（sphCookie 失效/私密视频？）")
    return data


def get_feed_info(export_id, general_token):
    rid = _generate_rid()
    api = (FEED_INFO_URL + f"?_rid={rid}"
           "&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed")
    referer = ("https://channels.weixin.qq.com/finder-preview/pages/feed"
               "?entry_card_type=48&comment_scene=39&appid=0"
               f"&token={urllib.parse.quote(general_token)}&entry_scene=0&eid={urllib.parse.quote(export_id)}")
    headers = dict(FEED_INFO_HEADERS, Referer=referer)
    return _post(api, headers, {"baseReq": {"generalToken": general_token}, "exportId": export_id})


def fetch_video_profile(share_url, cookie):
    data = parse_share_url(share_url, cookie)
    token, eid = "", ""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(data.get("playable_url", "")).query)
        token = (q.get("token") or [""])[0]
        eid = (q.get("eid") or [""])[0]
    except Exception:
        pass
    return get_feed_info(eid, token)


def resolve(share_url, cookie):
    """返回 (videoUrl, description)。"""
    feed = fetch_video_profile(share_url, cookie)
    fi = (feed.get("data") or {}).get("feedInfo") or {}
    src = ((fi.get("h264VideoInfo") or {}).get("videoUrl")
           or (fi.get("h265VideoInfo") or {}).get("videoUrl")
           or fi.get("videoUrl") or "")
    return src, fi.get("description", "")
