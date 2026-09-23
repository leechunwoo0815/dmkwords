# scripts/wechat_ide_mcp.py — 微信开发者工具自查通道（薄封装，2026-09-21 重写）
"""用法：python scripts/wechat_ide_mcp.py <工具名> '<JSON参数>'
示例：
  python scripts/wechat_ide_mcp.py check_wechatide_status
  python scripts/wechat_ide_mcp.py simulator_screenshot '{"path":"/tmp/a.jpg","wait":3}'
  python scripts/wechat_ide_mcp.py simulator_open_page '{"page":"pages/books/books","query":"child_id=2"}'
  python scripts/wechat_ide_mcp.py automation_evaluate '{"fnSource":"function(){return 1}"}'

**2026-09-21 重写原因**：旧版把 `wechatide mcp`（stdio JSON-RPC）当唯一通道，但新版
CLI（v0.3.11）已不再提供该子命令——直连报 `BrokenPipeError`（本轮实测）。现按官方形状
直呼 CLI：`wechatide -c <client> <tool> --flag value`。

参数约定：JSON 键 snake_case / camelCase 一律转 `--kebab-case`；布尔 true 作开关
（`--optimize`），false 省略；`project` 缺省为本仓 miniapp 目录。
授权：首次连接 IDE 弹「MCP 客户端授权」——**必须用户点「允许」**（模型点不了，
实测 osascript 被系统拒绝辅助访问 -25211）。自查流程见 docs/15 §二十。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

CLI = "/usr/local/bin/wechatide"
CLIENT = "zcode"
DEFAULT_PROJECT = "/Users/litianyu/cc-projects/dmkwords/miniapp"


def to_flag(key: str) -> str:
    """fnSource → --fn-source；scroll_top → --scroll-top。"""
    return "--" + re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), key).replace("_", "-")


def build_args(tool: str, params: dict) -> list[str]:
    args = [CLI, "-c", CLIENT, tool]
    merged = {"project": DEFAULT_PROJECT, **params}
    for key, value in merged.items():
        if isinstance(value, bool):
            if value:
                args.append(to_flag(key))
        elif value is not None:
            args += [to_flag(key), str(value)]
    return args


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tool = sys.argv[1]
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    proc = subprocess.run(build_args(tool, params), capture_output=True, text=True, timeout=600)
    out = proc.stdout or ""
    start = out.find("{")  # CLI 先打一行 skill-call 日志，剥掉后便于调用方直接解析 JSON
    sys.stdout.write(out[start:] if start >= 0 else out)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-500:])
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
