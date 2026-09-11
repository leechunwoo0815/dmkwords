# tests/unit/test_wm15_assets.py — WM15 视觉资产三端同步断言（D4 纪律机械化）
"""消费点清单纪律的机械版：**后端常量是唯一事实源**，三端资源目录的文件名集合
必须与之严格相等——任何"只补了一端"或"常量加了没出图"都会在这里被拦下。

为什么值得一条测试：头像/勋章是"本地包资源"（零 token 零网络），一旦三端不一致，
小程序图裂、管理端选择器空白、海报合成失败，而这三处分散在两个语言三个目录里，
靠人眼核对必然漏（媒体消费点断链族已有 7 犯前科）。
"""

from __future__ import annotations

import os

from backend.domain.reading_circle.art import AVATAR_IDS, BADGE_IDS

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

AVATAR_DIRS = [
    os.path.join(ROOT, "backend", "assets", "avatars"),
    os.path.join(ROOT, "miniapp", "icons", "avatars"),
    os.path.join(ROOT, "admin-web", "public", "avatars"),
]
BADGE_DIRS = [
    os.path.join(ROOT, "backend", "assets", "badges"),
    os.path.join(ROOT, "miniapp", "icons", "badges"),
    os.path.join(ROOT, "admin-web", "public", "badges"),
]


def _ids_in(d: str) -> set[str]:
    return {f[:-4] for f in os.listdir(d) if f.endswith(".png")}


def test_avatar_ids_match_all_three_ends() -> None:
    expect = set(AVATAR_IDS)
    assert len(expect) == 24, "12 动物 × 2 配色 = 24"
    for d in AVATAR_DIRS:
        assert _ids_in(d) == expect, f"{os.path.relpath(d, ROOT)} 与 AVATAR_IDS 不一致"


def test_badge_ids_match_all_three_ends() -> None:
    expect = set(BADGE_IDS)
    assert len(expect) == 9, "里程碑 6 + 等级模板 1 + 连击 2 = 9"
    for d in BADGE_DIRS:
        assert _ids_in(d) == expect, f"{os.path.relpath(d, ROOT)} 与 BADGE_IDS 不一致"
