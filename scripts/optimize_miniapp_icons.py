#!/usr/bin/env python3
"""小程序主包图标瘦身（E-20260915：主包 2731KB > 2048KB 上限）。

背景：`avatars/` `badges/` `frames/` `special/` 里是 256/440 px 的大图，
而显示尺寸仅 32~100 px（5~8 倍冗余）。**文件名与扩展名保持不变**——只降采样 + 优化编码，
故**零代码改动、零引用变更**（动态拼路径 /icons/avatars/${id}.png 照旧可用）。

用法：
  python scripts/optimize_miniapp_icons.py            # dry-run，只报告
  python scripts/optimize_miniapp_icons.py --apply    # 落盘
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "miniapp" / "icons"

# 目录 → 目标最长边（显示尺寸的 ~2~3 倍，兼顾 2x/3x 屏）
TARGETS = {
    "avatars": 160,
    "badges": 160,
    "frames": 220,
    "special": 220,
}
APPLY = "--apply" in sys.argv


def optimize(path: Path, max_side: int) -> tuple[int, int]:
    """返回 (优化前字节, 优化后字节)。不放大，只缩小 + 优化编码。"""
    before = path.stat().st_size
    with Image.open(path) as im:
        im.load()
        w, h = im.size
        scale = max_side / max(w, h)
        if scale < 1:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        out = ROOT / "tmp-icon-opt.png"
        im.save(out, format="PNG", optimize=True)
    after = out.stat().st_size
    if APPLY and after < before:
        out.replace(path)
        return before, after
    out.unlink(missing_ok=True)
    return before, after


def main() -> int:
    print("模式:", "APPLY（落盘）" if APPLY else "DRY-RUN（仅报告）")
    total_before = total_after = 0
    for sub, max_side in TARGETS.items():
        d = ICONS / sub
        if not d.is_dir():
            continue
        b = a = 0
        for f in sorted(d.glob("*.png")):
            x, y = optimize(f, max_side)
            b += x
            a += y
        total_before += b
        total_after += a
        print(
            f"  {sub:<10} {len(list(d.glob('*.png'))):>3} 个  {b // 1024:>5}KB → {a // 1024:>5}KB"
        )

    other = sum(f.stat().st_size for f in ICONS.glob("*.png"))
    total_before += other
    total_after += other

    # 主包估算：icons + pages + components + utils + custom-tab-bar
    def dsize(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    rest = sum(
        dsize(ROOT / "miniapp" / x) for x in ("pages", "components", "utils", "custom-tab-bar")
    )
    main_before = (total_before + rest) / 1024
    main_after = (total_after + rest) / 1024
    print(f"\n  icons 合计 {total_before // 1024}KB → {total_after // 1024}KB")
    print(f"  主包估算 {main_before:.0f}KB → {main_after:.0f}KB（上限 2048KB）")
    print(f"  {'✅ 达标' if main_after <= 2048 else '❌ 仍超限'}")
    return 0 if main_after <= 2048 else 1


if __name__ == "__main__":
    raise SystemExit(main())
