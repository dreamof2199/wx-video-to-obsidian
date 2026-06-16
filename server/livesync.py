#!/usr/bin/env python3
# 经本地 obsidian-sync-mcp（MCP Streamable-HTTP）把笔记加密写入 LiveSync CouchDB
import json
import os
import urllib.request

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8787/mcp")


def _post(body, session=None):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(MCP_URL, data=data, headers=headers)
    resp = urllib.request.urlopen(req, timeout=90)
    sid = resp.headers.get("mcp-session-id") or session
    raw = resp.read().decode(errors="replace")
    result = None
    if raw.strip().startswith("{"):
        result = json.loads(raw)
    else:
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    result = json.loads(line[5:].strip())
                except Exception:
                    pass
    return sid, result


def call_tool(tool, args):
    sid, _ = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "clip-server", "version": "1"}}})
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": tool, "arguments": args}}, sid)
    if res and "error" in res:
        raise RuntimeError(f"MCP {tool} 失败: {res['error']}")
    return res


def write_note(path, content):
    res = call_tool("write_note", {"path": path, "content": content})
    txt = res["result"]["content"][0]["text"]
    return txt
