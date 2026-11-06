# tests/unit/test_circle_likers_unbounded_20261106.py — 点赞头像墙返回全部点赞
#
# 产品口径（2026-11-06 用户拍板）：信息流点赞头像**每行 8 个、不限行数**——
# 后端默认不再按每帖 8 条截断，返回该页全部点赞；`limit=` 仍保留给需要限量的调用方
# （原 P3 性能判据 `test_likers_map_fetches_at_most_limit_per_post` 继续守住 limit 分支）。

from __future__ import annotations

from backend.database import SessionLocal


def _child(db, phone: str, name: str):
    from backend.domain.identity.models import Child, Parent

    p = Parent(name="点赞墙家长", phone=phone)
    db.add(p)
    db.flush()
    c = Child(name=name, parent_id=p.id)
    db.add(c)
    db.commit()
    return p, c


def test_likers_map_returns_all_by_default() -> None:
    from backend.domain.reading_circle.models import CircleLike, CirclePost
    from backend.domain.reading_circle.service import CircleService

    db = SessionLocal()
    try:
        p, c = _child(db, "13900009401", "点赞墙孩")
        post = CirclePost(
            child_id=c.id, parent_id=p.id, card_type="milestone", ref_id=2101, card_data="{}"
        )
        db.add(post)
        db.flush()
        for i in range(12):
            _, liker = _child(db, f"139000094{10 + i}", f"墙孩{i}")
            db.add(CircleLike(post_id=post.id, child_id=liker.id, parent_id=liker.parent_id))
        db.commit()

        all_likers = CircleService(db)._likers_map([post.id])[post.id]
        assert len(all_likers) == 12, f"默认应返回全部 12 个，实得 {len(all_likers)}"

        capped = CircleService(db)._likers_map([post.id], limit=8)[post.id]
        assert len(capped) == 8, f"显式 limit=8 应仍生效，实得 {len(capped)}"
    finally:
        db.close()


def test_likers_map_admin_first_and_included_in_all() -> None:
    from backend.domain.reading_circle.models import CircleLike, CirclePost
    from backend.domain.reading_circle.service import CircleService

    db = SessionLocal()
    try:
        p, c = _child(db, "13900009501", "馆长墙孩")
        post = CirclePost(
            child_id=c.id,
            parent_id=p.id,
            card_type="milestone",
            ref_id=2102,
            card_data="{}",
            admin_liked=1,
            like_count=4,
        )
        db.add(post)
        db.flush()
        for i in range(3):
            _, liker = _child(db, f"139000095{10 + i}", f"馆长墙孩{i}")
            db.add(CircleLike(post_id=post.id, child_id=liker.id, parent_id=liker.parent_id))
        db.commit()

        likers = CircleService(db)._likers_map([post.id])[post.id]
        assert len(likers) == 4, f"馆长 + 3 个孩子应 4 条，实得 {len(likers)}"
        assert likers[0]["is_admin"] is True
        assert all(x["is_admin"] is False for x in likers[1:])
    finally:
        db.close()
