# scripts/wechat_ide_mcp.py — 官方 wechatide mcp 通用调用客户端（Nightly 2.02+ 唯一可靠通道）
"""用法：python scripts/wechat_ide_mcp.py <工具名> '<JSON参数>'
示例：
  python scripts/wechat_ide_mcp.py automation_runtime_info '{"project":"/Users/litianyu/cc-projects/dmkwords/miniapp","action":"pageStack"}'
  python scripts/wechat_ide_mcp.py compile_wxml '{"project":"...","filePath":"pages/books/books.wxml"}'
前提：微信开发者工具已打开项目且安全设置→服务端口已开启；首次连接在 IDE 弹窗点允许。
背景：Nightly 2.02 废弃 cli auto --auto-port 端口注入（wechat-automation/helper.mjs 已失效），
官方通道即 wechatide mcp（stdio JSON-RPC）。项目路径默认 dmkwords/miniapp。
"""
import json, subprocess, sys, time

p = subprocess.Popen(["/usr/local/bin/wechatide", "mcp"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()

def recv_until(want_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = p.stdout.readline()
        if not line: break
        line = line.strip()
        if not line: continue
        try: msg = json.loads(line)
        except json.JSONDecodeError: continue
        if msg.get("id") == want_id: return msg
    return {"error": "timeout"}

send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "probe", "version": "0.0.1"}}})
recv_until(1)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

tool = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
args.setdefault("project", "/Users/litianyu/cc-projects/dmkwords/miniapp")
send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}})
r = recv_until(2)
for c in r.get("result", {}).get("content", []):
    if c.get("type") == "text":
        print(c["text"][:5000])
if not r.get("result"):
    print(json.dumps(r, ensure_ascii=False)[:2000])
p.terminate()
