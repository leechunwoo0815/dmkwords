# tests/unit/test_review11_r11e_20261026.py — 第十一轮整改 R11-e（第一百二十五批）
"""CI 自证首批：种子封面字体在缺少 macOS 字体的机器（Linux runner）上必须回退默认字体。

RED（还原 `ImageFont.truetype` 直调）：CI 的 `test_seed_demo_library_revives_soft_deleted_book`
报 `OSError: cannot open resource`。
"""

from __future__ import annotations

from scripts.seed_demo_library import _load_font


def test_seed_font_falls_back_when_path_missing():
    font = _load_font("/nonexistent/definitely-not-a-font.ttc", 20)
    assert font is not None
