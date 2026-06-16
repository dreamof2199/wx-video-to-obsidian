#!/usr/bin/env python3
# 极简 MCP Streamable-HTTP 客户端：调用 obsidian-sync-mcp 的一个工具
# 用法: python3 mcp_call.py <tool> '<json-args>'
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8787/mcp"

def post(body, session=None):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(BASE, data=data, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=90)
    except urllib.error.HTTPError as e:
        return None, {"http_error": e.code, "body": e.read().decode(errors="replace")[:500]}
    sid = resp.headers.get("mcp-session-id") or session
    raw = resp.read().decode(errors="replace")
    result = None
    if raw.strip().startswith("{"):
        result = json.loads(raw)
    else:  # SSE: 取最后一个 data: JSON
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    result = json.loads(line[5:].strip())
                except Exception:
                    pass
    return sid, result

def main():
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    sid, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "clip", "version": "1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": tool, "arguments": args}}, sid)
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
