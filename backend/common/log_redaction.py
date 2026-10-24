# backend/common/log_redaction.py — 敏感参数日志脱敏（第九轮 P1-4）
"""为什么单开：微信 AppSecret / access_token 只能走 **URL query**（微信侧接口就是这么设计的），
而 `httpx` 在 INFO 级把**完整 URL** 打进进程日志 → 能读日志的人（运维 / 日志采集平台 /
误提交的日志附件）就能拿到小程序主凭据去换 access_token。

`uvicorn.access` 侧早就有 `main._RedactQueryString`（第八轮 P2-16，媒体 `?token=` 泄漏），
但 httpx 是**另一条通道**：请求侧日志由 `httpx` logger 打（`HTTP Request: GET <url> ...`），
`uvicorn.access` 的过滤器管不到它。

口径与 uvicorn 侧一致：**脱敏而不是关日志**——保留 host/path/状态码可查，只抹参数值。

**第十轮 W4-D1（2026-10-24）修的是什么**：httpx 打日志时传进 `record.args` 的是
**`httpx.URL` 对象**，不是 `str`；旧过滤器只对 `isinstance(a, str)` 的 arg 做替换 →
真实调用路径**一个字符都没抹**（单测却绿，因为它手搓了一个字符串 arg，形状与线上不同）。
后果实测：`gate-runs/**/gate-*.log` 里躺着 18 个仍未过期的家长 JWT 明文（`?token=eyJ...`）。
现在非数值 arg 一律先 `str()` 再脱敏，形状差异不再是漏点。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

#: 需要抹值的 query 参数名（微信系主凭据 + 项目自身习惯的 token 类参数）
SENSITIVE_QUERY_KEYS: tuple[str, ...] = (
    "secret",
    "appsecret",
    "access_token",
    "refresh_token",
    "js_code",
    "session_key",
    "token",
)

_REDACT_RE = re.compile(
    r"(?i)\b(" + "|".join(SENSITIVE_QUERY_KEYS) + r")=[^&\s\"']+",
)


def redact_sensitive(text: str) -> str:
    """抹掉文本里敏感 query 参数的值（其余原样保留）。"""
    return _REDACT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


def _redact_arg(value: object) -> object:
    """把单个日志 arg 脱敏。

    **非数值一律 `str()` 后脱敏**（W4-D1）：httpx 传 `httpx.URL` 对象，
    旧实现只认 `str` → 真实路径静默漏。数值保留原类型（`%d` 占位不受影响），
    映射/序列**保形**（`%(k)s` 占位靠"单元素 tuple 里放 mapping"生效，
    把它 str 掉会让 `msg % args` 直接抛 TypeError）。
    """
    if value is None or isinstance(value, (int, float, bytes)):
        return value
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, Mapping):
        return {k: _redact_arg(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(_redact_arg(v) for v in value)
    return redact_sensitive(str(value))


class RedactSensitiveLogFilter(logging.Filter):
    """把记录里的 `msg` 与 `args` 都过一遍脱敏（httpx 用的是 %s 占位 + args 传 URL）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        elif record.args:
            record.args = _redact_arg(record.args)
        return True


#: 需要挂脱敏的 logger 名（httpx 打请求行；httpcore 打连接/调试行）
_REDACT_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")


def install_log_redaction() -> None:
    """给 httpx / httpcore 挂脱敏过滤器（**幂等**：重复调用不会重复挂）。"""
    for name in _REDACT_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactSensitiveLogFilter) for f in target.filters):
            target.addFilter(RedactSensitiveLogFilter())
