# tests/unit/test_media_health.py — 媒体体检：防误删断言（docs/15 §二十二）
"""这组测试守的是**"不会删掉正在使用的图片"**这条硬条件（用户 2026-09-23 原话）。

每条用例对应五道防线里的一道；任一条红 = 生产上可能把在用的图当垃圾清掉，
**改代码，别改断言**。防线出处：`docs/15 §22.2`，实现：`backend/domain/admin/media_service.py`。

注意：测试与 dev 共库（conftest TRUNCATE 业务表），但 uploads 是**测试专属临时目录**
（conftest 顶部 `UPLOADS_DIR` 隔离）——所以断言都锚在"我造的那张图"上，
不对全目录计数做绝对值断言（同会话其他测试可能也落了文件）。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta

import pytest

from backend.common.exceptions import ValidationError
from backend.common.media_paths import TRASH_DIRNAME
from backend.config import get_settings
from backend.database import Base, SessionLocal
from backend.domain.admin.media_models import MediaCensus, MediaTrashEntry
from backend.domain.admin.media_service import (
    REFERENCE_SPEC_EXEMPT,
    REFERENCE_SPECS,
    MediaHealthService,
    collect_referenced,
)
from backend.domain.catalog.models import Book

#: 名字像媒体路径的列（覆盖守卫的扫描口径）
_MEDIA_COL_RE = re.compile(
    r"(_path|_url|path|url|image|thumb|voucher|audio|cover|avatar|poster|photo|file|blocks)",
    re.I,
)


def _root() -> str:
    return os.path.abspath(get_settings().UPLOADS_DIR)


def _plant(rel: str, *, age_minutes: int = 120, size: int = 1024) -> str:
    """在 uploads 下造一个假文件（默认 2 小时前 mtime → 已过最小年龄闸门）。"""
    full = os.path.join(_root(), rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(b"x" * size)
    stamp = time.time() - age_minutes * 60
    os.utime(full, (stamp, stamp))
    return full


def _trash_path(*parts: str) -> str:
    return os.path.join(_root(), TRASH_DIRNAME, *parts)


def _entry(db, rel: str) -> MediaTrashEntry:
    return db.query(MediaTrashEntry).filter(MediaTrashEntry.rel_path == rel).one()


class _Admin:
    """服务层只读 admin 的 id/display_name/username，这里给个最小替身。"""

    id = 99
    display_name = "测试超管"
    username = "tester"


def _moved_paths(db, result) -> list[str]:
    """本批次实际移进回收站的路径（只锚定这次操作，不依赖全目录计数）。"""
    return [
        e.rel_path
        for e in db.query(MediaTrashEntry).filter(MediaTrashEntry.batch == result["batch"]).all()
    ]


# ---------------- 覆盖守卫（防未来漂移：新增带图列漏登记 → 在用图被判孤儿） ----------------


def test_reference_coverage_guard(db):
    """模型里所有"像媒体路径"的列，必须登记进 REFERENCE_SPECS 或显式豁免。"""
    covered = {f"{spec.model.__tablename__}.{spec.attr}" for spec in REFERENCE_SPECS}
    matched: set[str] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if _MEDIA_COL_RE.search(column.name):
                matched.add(f"{table.name}.{column.name}")

    unregistered = matched - covered - set(REFERENCE_SPEC_EXEMPT)
    assert not unregistered, (
        f"这些列名字像媒体路径却没登记（会漏进引用集 → 在用的文件被判孤儿）：{sorted(unregistered)}。"
        "请加进 media_service.REFERENCE_SPECS，或在 REFERENCE_SPEC_EXEMPT 写明它为什么不是文件。"
    )
    for key, reason in REFERENCE_SPEC_EXEMPT.items():
        assert key in matched, f"豁免条目 {key} 在模型里不存在（幻覚条目）"
        assert reason, f"豁免条目 {key} 必须写明理由"
    for spec in REFERENCE_SPECS:
        assert hasattr(spec.model, spec.attr), f"{spec.label}: {spec.model}.{spec.attr} 不存在"


# ---------------- 盘点 ----------------


def test_scan_splits_referenced_protected_and_orphan(db):
    used = _plant("cover/1111/used.jpg")
    orphan = _plant("cover/1111/orphan.jpg", size=2048)
    protected = _plant("voucher/o1_abc.jpg")
    outside = _plant("posters/child_1.jpg")  # 白名单外 → 不算孤儿（保守）
    db.add(Book(title="在用的书", isbn="9780000000001", cover_path="cover/1111/used.jpg"))
    db.commit()

    scan = MediaHealthService(db).scan()
    assert os.path.isfile(used) and os.path.isfile(orphan)
    assert os.path.isfile(protected) and os.path.isfile(outside)
    assert "cover/1111/used.jpg" in collect_referenced(db)
    # 锚在"我这几张"上：uploads 是全会话共用的临时目录，不能对全量计数做绝对值断言
    assert "cover/1111/orphan.jpg" in scan.orphan_paths
    assert "cover/1111/used.jpg" not in scan.orphan_paths  # 在用的不算孤儿
    assert "voucher/o1_abc.jpg" not in scan.orphan_paths  # 保护目录不算孤儿
    assert "posters/child_1.jpg" not in scan.orphan_paths  # 白名单外不算孤儿
    assert scan.orphan_bytes >= 2048
    cover_bucket = next(b for b in scan.breakdown if b["bucket"] == "cover/")
    assert cover_bucket["files"] >= 1 and cover_bucket["bytes"] >= 2048
    assert scan.protected_total >= 1  # 凭证被保护


# ---------------- 防线 1/2：引用中的图、保护目录、白名单外一律拒绝 ----------------


def test_trash_refuses_in_use_image(db):
    """**核心断言**：被 DB 引用的图，无论谁点按钮都不许动。"""
    _plant("cover/2222/used.jpg")
    _plant("cover/2222/orphan.jpg")
    db.add(Book(title="在用的书", isbn="9780000000002", cover_path="cover/2222/used.jpg"))
    db.commit()

    result = MediaHealthService(db).trash(
        ["cover/2222/used.jpg", "cover/2222/orphan.jpg"], _Admin(), "测试清理"
    )
    reasons = {s["path"]: s["reason"] for s in result["skipped"]}
    assert "正在使用" in reasons["cover/2222/used.jpg"]
    assert os.path.isfile(os.path.join(_root(), "cover/2222/used.jpg"))
    assert not os.path.isfile(os.path.join(_root(), "cover/2222/orphan.jpg"))
    assert "cover/2222/orphan.jpg" in _moved_paths(db, result)


def test_trash_refuses_protected_and_outside_whitelist(db):
    for rel in (
        "voucher/o2_v.jpg",
        "observation/child_9/abc.jpg",
        "activity_detail/9_x.jpg",
        "posters/child_2.jpg",
        "logs/old.jpg",  # 既不在保护名单、也不在可再生白名单
        "book_audio/9780000000003/audio_x.mp3",
    ):
        _plant(rel)

    result = MediaHealthService(db).trash(
        [
            "voucher/o2_v.jpg",
            "observation/child_9/abc.jpg",
            "activity_detail/9_x.jpg",
            "posters/child_2.jpg",
            "logs/old.jpg",
            "book_audio/9780000000003/audio_x.mp3",
            "../secrets.txt",
        ],
        _Admin(),
        "测试清理",
    )
    reasons = {s["path"]: s["reason"] for s in result["skipped"]}
    assert "保护目录" in reasons["voucher/o2_v.jpg"]
    assert "保护目录" in reasons["observation/child_9/abc.jpg"]
    assert "保护目录" in reasons["activity_detail/9_x.jpg"]
    assert "保护目录" in reasons["posters/child_2.jpg"]
    assert "白名单" in reasons["logs/old.jpg"]
    assert "路径非法" in reasons["../secrets.txt"]
    # 白名单内且无引用的音频**可以**动（说明拒绝不是"一刀切全拒"）
    assert "book_audio/9780000000003/audio_x.mp3" not in reasons
    assert "book_audio/9780000000003/audio_x.mp3" in _moved_paths(db, result)
    for rel in (
        "voucher/o2_v.jpg",
        "observation/child_9/abc.jpg",
        "activity_detail/9_x.jpg",
        "logs/old.jpg",
    ):
        assert os.path.isfile(os.path.join(_root(), rel))


# ---------------- 防线 3：最小年龄（防"先写文件、后落库引用"的竞态） ----------------


def test_trash_refuses_file_younger_than_min_age(db):
    _plant("cover/3333/fresh.jpg", age_minutes=1)
    result = MediaHealthService(db).trash(["cover/3333/fresh.jpg"], _Admin(), "测试清理")
    assert result["moved"] == 0
    assert "分钟" in result["skipped"][0]["reason"]
    assert os.path.isfile(os.path.join(_root(), "cover/3333/fresh.jpg"))


def test_trash_requires_reason(db):
    _plant("cover/3334/orphan.jpg")
    with pytest.raises(ValidationError):
        MediaHealthService(db).trash(["cover/3334/orphan.jpg"], _Admin(), "   ")
    assert os.path.isfile(os.path.join(_root(), "cover/3334/orphan.jpg"))


# ---------------- 防线 4：只移动不删除 + 还原 ----------------


def test_trash_moves_to_bin_and_restores(db):
    _plant("cover/4444/orphan.jpg", size=4096)
    service = MediaHealthService(db)
    before = service.scan().orphan_files

    result = service.trash(None, _Admin(), "清理演示孤儿图", all_orphans=True)
    assert "cover/4444/orphan.jpg" in _moved_paths(db, result)
    assert not os.path.isfile(os.path.join(_root(), "cover/4444/orphan.jpg"))
    trash_batch = result["batch"]
    assert os.path.isfile(_trash_path(trash_batch, "cover/4444/orphan.jpg"))
    assert result["moved_bytes"] >= 4096
    # 清完立刻刷新数字（看板不再显示脏数）
    assert result["census"]["trigger"] == MediaCensus.TRIGGER_AFTER_TRASH
    assert result["census"]["orphan_files"] < before

    entry = _entry(db, "cover/4444/orphan.jpg")
    assert entry.state == MediaTrashEntry.STATE_TRASHED
    assert entry.actor_name == "测试超管"
    assert entry.restore_until and entry.restore_until > datetime.now()

    restored = service.restore(entry.id, _Admin())
    assert restored["rel_path"] == "cover/4444/orphan.jpg"
    assert os.path.isfile(os.path.join(_root(), "cover/4444/orphan.jpg"))
    assert not os.path.isfile(_trash_path(trash_batch, "cover/4444/orphan.jpg"))
    db.refresh(entry)
    assert entry.state == MediaTrashEntry.STATE_RESTORED
    assert restored["census"]["trigger"] == MediaCensus.TRIGGER_AFTER_RESTORE


def test_restore_refuses_when_original_occupied(db):
    _plant("cover/4445/orphan.jpg")
    service = MediaHealthService(db)
    service.trash(["cover/4445/orphan.jpg"], _Admin(), "清理")
    entry = _entry(db, "cover/4445/orphan.jpg")
    _plant("cover/4445/orphan.jpg")  # 有人又把同路径填上了
    with pytest.raises(ValidationError):
        service.restore(entry.id, _Admin())
    assert os.path.isfile(os.path.join(_root(), "cover/4445/orphan.jpg"))


# ---------------- 防线 5：到期清除前再复检引用 ----------------


def test_purge_restores_file_that_became_referenced_again(db):
    _plant("cover/5555/again.jpg")
    service = MediaHealthService(db)
    service.trash(["cover/5555/again.jpg"], _Admin(), "清理")
    entry = _entry(db, "cover/5555/again.jpg")
    entry.restore_until = datetime.now() - timedelta(days=1)  # 假装已到期
    # 期间这张图**又被引用了**（例：某本书重新上传同一张图）
    db.add(Book(title="重新引用", isbn="9780000000005", cover_path="cover/5555/again.jpg"))
    db.commit()

    outcome = service.purge_expired()
    assert outcome["restored"] >= 1
    assert os.path.isfile(os.path.join(_root(), "cover/5555/again.jpg"))
    db.refresh(entry)
    assert entry.state == MediaTrashEntry.STATE_RESTORED


def test_purge_deletes_only_expired_unreferenced(db):
    _plant("cover/6666/expired.jpg", size=512)
    _plant("cover/6666/fresh.jpg", size=512)
    service = MediaHealthService(db)
    service.trash(["cover/6666/expired.jpg", "cover/6666/fresh.jpg"], _Admin(), "清理")
    expired = _entry(db, "cover/6666/expired.jpg")
    fresh = _entry(db, "cover/6666/fresh.jpg")
    expired.restore_until = datetime.now() - timedelta(days=1)
    fresh.restore_until = datetime.now() + timedelta(days=29)
    db.commit()

    outcome = service.purge_expired()
    assert outcome["purged"] >= 1 and outcome["purged_bytes"] >= 512
    db.refresh(expired)
    db.refresh(fresh)
    assert expired.state == MediaTrashEntry.STATE_PURGED
    assert fresh.state == MediaTrashEntry.STATE_TRASHED  # 未到期不动
    assert os.path.isfile(_trash_path(fresh.batch, "cover/6666/fresh.jpg"))


# ---------------- 防线 6（用户 2026-09-23 追加）：清空回收站 = 提前到期 ----------------


def test_empty_trash_purges_unreferenced_and_rescues_referenced(db):
    """清空回收站**不绕过防线**：仍被引用的那张会被当场救回原位，不会被删。"""
    _plant("cover/7000/gone.jpg", size=512)
    _plant("cover/7000/rescue.jpg", size=512)
    service = MediaHealthService(db)
    service.trash(["cover/7000/gone.jpg", "cover/7000/rescue.jpg"], _Admin(), "清理")
    rescued = _entry(db, "cover/7000/rescue.jpg")
    # 期间这张图又被引用了（回收站保留期内完全可能发生）
    db.add(Book(title="又被引用", isbn="9780000000007", cover_path="cover/7000/rescue.jpg"))
    db.commit()

    outcome = service.empty_trash(_Admin(), "测试清空回收站")
    assert outcome["purged"] == 1 and outcome["restored"] == 1
    assert outcome["purged_bytes"] >= 512
    assert not os.path.isfile(_trash_path(rescued.batch, "cover/7000/rescue.jpg"))
    assert os.path.isfile(os.path.join(_root(), "cover/7000/rescue.jpg"))  # 救回原位
    db.refresh(rescued)
    assert rescued.state == MediaTrashEntry.STATE_RESTORED
    assert _entry(db, "cover/7000/gone.jpg").state == MediaTrashEntry.STATE_PURGED
    assert outcome["census"]["trigger"] == MediaCensus.TRIGGER_AFTER_TRASH

    from backend.domain.admin.models import AuditLog

    log = db.query(AuditLog).filter(AuditLog.action == "media.purge").one()
    assert log.reason == "测试清空回收站"
    assert "cover/7000/rescue.jpg" in log.detail  # 被救回的条目留痕


def test_empty_trash_requires_reason_and_non_empty_bin(db):
    service = MediaHealthService(db)
    with pytest.raises(ValidationError):
        service.empty_trash(_Admin(), "   ")  # 原因必填
    with pytest.raises(ValidationError) as exc:
        service.empty_trash(_Admin(), "空箱清空")
    assert "空" in str(exc.value)


# ---------------- 记账对账（清场重建会抹掉回收站记账 → 自动接管，**不删文件**） ----------------


def test_reconcile_adopts_unmanaged_trash_files(db):
    """模拟"文件在回收站、账被清空"（清场重建的真实形态）：对账是**补账**，不是删。"""
    _plant("cover/8000/lost.jpg", size=640)
    with SessionLocal() as probe:
        batch = MediaHealthService(probe).trash(["cover/8000/lost.jpg"], _Admin(), "清理")["batch"]
    trashed = _trash_path(batch, "cover/8000/lost.jpg")
    assert os.path.isfile(trashed)

    # 抹掉记账（等价于清场重建把 media_trash_entries 清空）
    db.query(MediaTrashEntry).delete()
    db.commit()
    service = MediaHealthService(db)
    unmanaged = [(rel, size) for _batch, rel, size in service.unmanaged_trash_files()]
    assert ("cover/8000/lost.jpg", 640) in unmanaged

    outcome = service.reconcile_trash()
    assert outcome["adopted"] >= 1 and outcome["rescued"] == 0
    assert os.path.isfile(trashed)  # 文件仍在：对账只补账
    entry = _entry(db, "cover/8000/lost.jpg")
    assert entry.batch == batch and entry.state == MediaTrashEntry.STATE_TRASHED
    assert entry.actor_name == "系统·对账接管"
    assert entry.restore_until and entry.restore_until > datetime.now()
    # 幂等：再跑一次不重复记账
    again = service.reconcile_trash()
    assert again["adopted"] == 0
    assert (
        db.query(MediaTrashEntry).filter(MediaTrashEntry.rel_path == "cover/8000/lost.jpg").count()
        == 1
    )


def test_reconcile_rescues_referenced_file_missing_from_uploads(db):
    """对账时若发现回收站里的文件**又被引用了**、且 uploads 已无此文件 → 搬回原位（救回）。"""
    _plant("cover/8100/back.jpg", size=256)
    with SessionLocal() as probe:
        batch = MediaHealthService(probe).trash(["cover/8100/back.jpg"], _Admin(), "清理")["batch"]
    db.query(MediaTrashEntry).delete()
    db.add(Book(title="又被引用", isbn="9780000000008", cover_path="cover/8100/back.jpg"))
    db.commit()

    outcome = MediaHealthService(db).reconcile_trash()
    assert outcome["rescued"] == 1 and outcome["conflicted"] == 0
    assert os.path.isfile(os.path.join(_root(), "cover/8100/back.jpg"))
    assert not os.path.isfile(_trash_path(batch, "cover/8100/back.jpg"))


# ---------------- 任务与审计 ----------------


def test_media_census_task_records_report_and_logs_run(db):
    _plant("cover/7777/orphan.jpg")
    from backend.tasks.registry import TASKS, run_task

    spec = TASKS["media_census"]
    assert spec.cron_expr == "0 8 * * *"  # 每天早上 08:00（用户要求"每天早上出报告"）
    result = run_task("media_census")
    assert result["status"] == "success"
    row = db.query(MediaCensus).order_by(MediaCensus.id.desc()).first()
    assert row is not None
    assert row.trigger == MediaCensus.TRIGGER_SCHEDULED
    assert result["processed"] == row.orphan_files  # 看板数字 = 落库报告里的孤儿数
    assert row.orphan_files >= 1


def test_cron_catchup_after_sleep_or_restart(db):
    """钟点任务的"迟到"两道保险：宽限 12h（进程只睡了）+ 启动补跑（进程当时不在）。

    2026-09-24 实测：Mac 睡过 08:00，APScheduler 记为 missed 且超过 120s 宽限被丢弃 →
    当天根本没出报告。这两道保险合起来才让"每天早上出一份报告"站得住。
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from backend.common.notification_models import TaskRunLog
    from backend.tasks.registry import (
        CRON_MISFIRE_GRACE_SECONDS,
        _schedule_cron_catchup,
        cron_due_today,
    )

    # ① 宽限足够大（休眠醒来能补）
    assert CRON_MISFIRE_GRACE_SECONDS >= 3600
    # ② 判定"今天该跑时刻"的纯逻辑（用固定时刻，不依赖测试运行时的真实钟点）
    assert cron_due_today("0 8 * * *", datetime(2026, 9, 24, 9, 44)) == datetime(2026, 9, 24, 8, 0)
    assert cron_due_today("0 8 * * *", datetime(2026, 9, 24, 7, 0)) is None  # 还没到
    assert cron_due_today("*/5 * * * *", datetime(2026, 9, 24, 9, 44)) is None  # 非固定钟点不补

    # ③ 启动补跑：只有"今天 08:00 已过且今日无成功记录"才补
    now = datetime.now()
    if now.hour < 8:
        pytest.skip("本地时间未过 08:00——补跑语义不存在（判定逻辑已在 ② 用固定时刻覆盖）")
    db.query(TaskRunLog).delete()
    db.commit()
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _schedule_cron_catchup(scheduler)
    assert "media_census-catchup" in {job.id for job in scheduler.get_jobs()}

    scheduler2 = BackgroundScheduler(timezone="Asia/Shanghai")
    db.add(
        TaskRunLog(
            task_name="media_census",
            started_at=now,
            status=TaskRunLog.STATUS_SUCCESS,
            processed=1,
        )
    )
    db.commit()
    _schedule_cron_catchup(scheduler2)
    assert "media_census-catchup" not in {job.id for job in scheduler2.get_jobs()}


def test_trash_writes_audit_log(db):
    from backend.domain.admin.models import AuditLog

    _plant("cover/8888/orphan.jpg")
    MediaHealthService(db).trash(["cover/8888/orphan.jpg"], _Admin(), "清理演示孤儿图")
    log = db.query(AuditLog).filter(AuditLog.action == "media.trash").one()
    assert log.reason == "清理演示孤儿图"
    assert "cover/8888/orphan.jpg" in log.detail


# ---------------- API 与权限（专员只读 / 仅超管可清理） ----------------


def test_api_health_visible_to_staff(client, staff_headers):
    resp = client.get("/api/admin/media/health", headers=staff_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trash"]["retain_days"] >= 1
    assert body["trash"]["min_age_minutes"] >= 1
    assert body["trash"]["items"] == []


def test_api_trash_and_restore_superadmin_only(client, admin_headers, staff_headers):
    _plant("cover/9999/orphan.jpg")
    payload = {"reason": "测试清理", "all_orphans": False, "paths": ["cover/9999/orphan.jpg"]}
    # 专员：看得到、删不动
    assert (
        client.post("/api/admin/media/trash", json=payload, headers=staff_headers).status_code
        == 403
    )
    # 超管：原因必填
    bad = dict(payload, reason="")
    assert client.post("/api/admin/media/trash", json=bad, headers=admin_headers).status_code == 422

    resp = client.post("/api/admin/media/trash", json=payload, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["moved"] == 1 and body["moved_bytes"] == 1024
    assert not os.path.isfile(os.path.join(_root(), "cover/9999/orphan.jpg"))

    with SessionLocal() as probe:
        entry_id = _entry(probe, "cover/9999/orphan.jpg").id

    assert (
        client.post(f"/api/admin/media/trash/{entry_id}/restore", headers=staff_headers).status_code
        == 403
    )
    restored = client.post(f"/api/admin/media/trash/{entry_id}/restore", headers=admin_headers)
    assert restored.status_code == 200, restored.text
    assert os.path.isfile(os.path.join(_root(), "cover/9999/orphan.jpg"))


def test_api_uploads_does_not_serve_trash(client, admin_headers):
    """第 4 道防线的配套：回收站里的文件不能被 Web 下发。"""
    _plant("cover/aaaa/orphan.jpg")
    with SessionLocal() as probe:
        result = MediaHealthService(probe).trash(["cover/aaaa/orphan.jpg"], _Admin(), "清理")
    trashed_rel = f"{result['batch']}/cover/aaaa/orphan.jpg"
    assert os.path.isfile(_trash_path(trashed_rel))
    resp = client.get(f"/api/admin/uploads/{TRASH_DIRNAME}/{trashed_rel}", headers=admin_headers)
    assert resp.status_code == 404


def test_api_empty_trash_superadmin_only(client, admin_headers, staff_headers):
    _plant("cover/bbbb/orphan.jpg")
    with SessionLocal() as probe:
        batch = MediaHealthService(probe).trash(["cover/bbbb/orphan.jpg"], _Admin(), "清理")[
            "batch"
        ]

    assert (
        client.post(
            "/api/admin/media/trash/empty", json={"reason": "专员尝试"}, headers=staff_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/media/trash/empty", json={"reason": ""}, headers=admin_headers
        ).status_code
        == 422
    )
    resp = client.post(
        "/api/admin/media/trash/empty", json={"reason": "测试清空"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["purged"] >= 1
    assert not os.path.isfile(_trash_path(batch, "cover/bbbb/orphan.jpg"))
