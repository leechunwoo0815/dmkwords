# tests/unit/test_fix44_media_scope.py — fix44 R3：媒体脚本三陷阱（Q8）回归锁
"""专家 Q8 认定的三个"守住了但靠运气"的陷阱，全部改成显式规则并在此锁死：

① `cleanup_uploads.is_protected` 的证据目录保护：原先 `PROTECTED_PREFIXES` 写 `"-samples/"`，
   而判定用 `rel.startswith(p)` —— 真实目录名是 `wm15-samples/…` → **永远匹配不到**，
   保护名存实亡（当时没出事只因它也不在可再生白名单里）。
② `cover/activity/` 与书封**同前缀但不同口径**，回压时必须分流；
③ 活动封面口径是 `activity_cover`(1200) 而非书封 `cover`(1080)。
"""

from __future__ import annotations

from scripts.cleanup_uploads import is_protected
from scripts.optimize_uploads import _UPLOAD_SCOPES, scope_of


def test_protected_evidence_dirs_match_by_suffix():
    """① 证据/审计目录必须被保护——按后缀匹配（wm15-samples 这类名字不再是漏网）。"""
    assert is_protected("wm15-samples/sheet.png")
    assert is_protected("fix34-samples/sub/deep.png")
    assert is_protected("-samples/x.png")
    assert is_protected("miniapp-audit-20260915/a.png")
    assert is_protected("voucher/ORD1_x.jpg")  # 人工上传，不可再生
    assert is_protected("observation/child_1/x.jpg")
    # 反向：可再生目录不得被误保护（否则清理脚本永远清不掉）
    assert not is_protected("cover/9780/9780_ab.jpg")
    assert not is_protected("circle/card_x.jpg")


def test_cover_activity_scope_split_from_book_cover():
    """②③ 活动封面与书封分流；③ 口径取 activity_cover（1200）而非 cover（1080）。"""
    assert scope_of("cover/activity/7_ab12.jpg") == "activity_cover"
    assert scope_of("cover/9780679824114/9780679824114_ab12.jpg") == "cover"
    assert scope_of("cover/local/5_ab12.jpg") == "cover"
    assert scope_of("voucher/ORD_1.jpg") == "doc"
    assert scope_of("observation/child_1/ab.jpg") == "doc"
    assert scope_of("posters/2.png") is None  # 不在口径表内 → 不处理
    # 最长前缀优先，且与字典顺序无关（顺序变了口径不得静默改错）
    for keys in (_UPLOAD_SCOPES, dict(reversed(list(_UPLOAD_SCOPES.items())))):
        assert max((p for p in keys if "cover/activity/1.jpg".startswith(p)), key=len) == (
            "cover/activity/"
        )


def test_image_policy_scopes_differ_on_edge():
    """口径本身必须真的不同（1080 vs 1200）——若哪天被改成同值，②③ 的分流就失去意义。"""
    from backend.common.file_storage import _POLICY_KEYS

    assert _POLICY_KEYS["cover"][0] == "image_cover_max_edge"
    assert _POLICY_KEYS["activity_cover"][0] == "image_activity_cover_max_edge"
    assert _POLICY_KEYS["cover"][1] != _POLICY_KEYS["activity_cover"][1]
