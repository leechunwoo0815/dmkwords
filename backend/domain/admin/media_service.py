# backend/domain/admin/media_service.py — 媒体体检：孤儿盘点 / 回收站（docs/15 §二十二）
"""uploads 磁盘与 DB 引用的对账（**引用口径唯一事实源**）。

为什么要有这一层（2026-09-23 用户裁定）：清理能力此前只在 `scripts/cleanup_uploads.py`（命令行、
默认 dry-run），而运营**只用管理端**——"看得到数字 + 点得动按钮"必须在 Web 层有一条通道。
本模块同时把**引用口径**收归一处：脚本改为 import 本模块（红线 51「同一口径只能有一份来源」；
两处各写一份的结局就是 R16a/R12 那次的误删，错误库 §一百零二）。

五道防线（规范 `docs/15 §22.2`，**改任何一条前先读规范**）：
  1. 引用集**即时复算**（不信任前端传参、不信任磁盘上昨天那份报告）
  2. 保护目录 + 可再生白名单**双重收窄**（不在白名单内一律不动）
  3. **最小年龄**（`media_trash_min_age_minutes`）——防"先写文件、后落库引用"两步之间的竞态
  4. **只移动、不物理删除**（`.trash/<批次>/`，可一键还原）
  5. 到期清除前**再复检一次引用**——复检发现在用则**自动还原**

权限与审计：查看 = `dashboard.view`（专员只读）；移入/还原 = 仅超管；审计动作 `media.trash` /
`media.restore`（原因必填）。到期自动清除无人工操作者，**不写审计**（走 `task_run_logs`）。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.media_paths import (  # noqa: F401 — 再导出，供脚本/测试沿用同一入口
    PROTECTED_PREFIXES,
    PROTECTED_SUFFIX_DIRS,
    REGENERABLE_PREFIXES,
    TRASH_DIRNAME,
    is_protected,
    is_regenerable,
    is_trash_path,
    norm_rel,
)
from backend.config import get_settings
from backend.domain.activity.models import Activity
from backend.domain.admin.media_models import MediaCensus, MediaTrashEntry
from backend.domain.catalog.models import Book
from backend.domain.identity.models import ObservationReport, Order
from backend.domain.reading_circle.models import CirclePost


@dataclass(frozen=True)
class ReferenceSpec:
    """一条"业务列存了 uploads 路径"的声明——**引用集的单一来源**。

    kind：`scalar` 单路径列 / `json_list` JSON 数组（如报告图） / `json_blocks` 图文块 JSON。
    新增带媒体列的表时**必须在这里加一行**，否则 `tests/unit/test_media_health.py` 的
    引用覆盖守卫会红（防"新表带图 → 在用的图被当孤儿"那族）。
    """

    label: str
    model: type
    attr: str
    kind: str = "scalar"


REFERENCE_SPECS: tuple[ReferenceSpec, ...] = (
    ReferenceSpec("书目封面", Book, "cover_path"),
    ReferenceSpec("书目音频", Book, "audio_path"),
    ReferenceSpec("活动封面", Activity, "cover_path"),
    ReferenceSpec("活动图文配图", Activity, "detail_blocks", kind="json_blocks"),
    ReferenceSpec("收款凭证", Order, "voucher_path"),
    ReferenceSpec("评估报告图", ObservationReport, "images", kind="json_list"),
    ReferenceSpec("阅读圈卡片图", CirclePost, "image_path"),
    ReferenceSpec("阅读圈缩略图", CirclePost, "thumb_path"),
)

#: 覆盖守卫的**显式豁免**（名字像媒体路径、实际不是文件）。守卫同样校验这里没有幻觉条目。
REFERENCE_SPEC_EXEMPT: dict[str, str] = {
    "books.audio_duration_seconds": "数字（音频时长），不是路径",
    "reading_progress.coverage_seconds": "数字（覆盖秒数），只是词根撞上 cover",
    "children.avatar": "头像**编号**（avatar_id 如 cat_sun），资产在小程序包/前端 public，不落 uploads",
    "media_censuses.files_total": "数字（盘点计数），不是路径",
    "media_censuses.orphan_files": "数字（盘点计数），不是路径",
    "media_trash_entries.rel_path": "回收站**出处**记录（已被移出 uploads 的路径）——算成引用会让"
    "「清完还能再清 / 还原」自锁，也把真实引用数撑虚",
}


@dataclass
class MediaScan:
    """一次盘点的原始结果（`record_census` 把它落库成一行报告）。"""

    files_total: int = 0
    referenced_total: int = 0
    protected_total: int = 0
    orphan_files: int = 0
    orphan_bytes: int = 0
    missing_refs: list[str] = field(default_factory=list)
    orphan_paths: list[str] = field(default_factory=list)
    breakdown: list[dict] = field(default_factory=list)


_norm = norm_rel  # 本模块内部短名（口径唯一来源在 backend/common/media_paths.py）


def collect_referenced(db: Session) -> set[str]:
    """DB 里所有被引用的 uploads 相对路径（**含软删行**——软删记录 purge 时才删文件）。

    来源 = `REFERENCE_SPECS`（单一事实源）。`scripts/cleanup_uploads.py` 与盘点/清理三处共用本函数。
    """
    rels: set[str] = set()
    for spec in REFERENCE_SPECS:
        for (value,) in db.query(getattr(spec.model, spec.attr)).all():
            if not value:
                continue
            if spec.kind == "scalar":
                rels.add(str(value))
            elif spec.kind == "json_list":
                try:
                    rels.update(str(x) for x in json.loads(value) if x)
                except (TypeError, ValueError):
                    continue
            elif spec.kind == "json_blocks":
                try:
                    for block in json.loads(value):
                        if isinstance(block, dict) and block.get("path"):
                            rels.add(str(block["path"]))
                except (TypeError, ValueError):
                    continue
    return {_norm(r) for r in rels if r}


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class MediaHealthService:
    """媒体体检服务：盘点 / 移入回收站 / 还原 / 到期清除。"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 基础 ----------

    def _root(self) -> str:
        return os.path.abspath(get_settings().UPLOADS_DIR)

    def _cfg_int(self, key: str, default: int) -> int:
        from backend.common.config_service import ConfigService

        return ConfigService(self.db).get_int(key, default)

    def trash_root(self) -> str:
        return os.path.join(self._root(), TRASH_DIRNAME)

    # ---------- 盘点 ----------

    def scan(self) -> MediaScan:
        """走一遍 uploads：分桶"DB 引用 / 保护目录 / 孤儿"（孤儿 = 不在引用集、不受保护、且在白名单内）。"""
        root = self._root()
        referenced = collect_referenced(self.db)
        scan = MediaScan(referenced_total=len(referenced))
        buckets: dict[str, list[int]] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath == root:
                # 回收站单独计量，不进盘点（否则"已清掉的图"会出现在孤儿里）
                dirnames[:] = [d for d in dirnames if d != TRASH_DIRNAME]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = _norm(os.path.relpath(full, root))
                scan.files_total += 1
                if rel in referenced:
                    continue
                if is_protected(rel):
                    scan.protected_total += 1
                    continue
                if not is_regenerable(rel):
                    continue  # 白名单外 → 不算孤儿（保守：宁可漏报不可误删）
                size = _file_size(full)
                scan.orphan_files += 1
                scan.orphan_bytes += size
                scan.orphan_paths.append(rel)
                bucket = rel.split("/", 1)[0] + "/"
                slot = buckets.setdefault(bucket, [0, 0])
                slot[0] += 1
                slot[1] += size
        scan.orphan_paths.sort()
        scan.breakdown = [
            {"bucket": b, "files": v[0], "bytes": v[1]} for b, v in sorted(buckets.items())
        ]
        scan.missing_refs = [
            r for r in sorted(referenced) if not os.path.isfile(os.path.join(root, r))
        ]
        return scan

    @staticmethod
    def _census_dict(row: MediaCensus) -> dict:
        try:
            breakdown = json.loads(row.breakdown) if row.breakdown else []
        except (TypeError, ValueError):
            breakdown = []
        return {
            "id": row.id,
            "trigger": row.trigger,
            "created_at": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
            "files_total": row.files_total,
            "referenced_total": row.referenced_total,
            "protected_total": row.protected_total,
            "orphan_files": row.orphan_files,
            "orphan_bytes": row.orphan_bytes,
            "missing_refs": row.missing_refs,
            "breakdown": breakdown,
        }

    def record_census(self, trigger: str) -> dict:
        """盘点 + 落库一行报告（**只读磁盘，不改任何文件**），返回该行。"""
        scan = self.scan()
        row = MediaCensus(
            trigger=trigger,
            files_total=scan.files_total,
            referenced_total=scan.referenced_total,
            protected_total=scan.protected_total,
            orphan_files=scan.orphan_files,
            orphan_bytes=scan.orphan_bytes,
            missing_refs=len(scan.missing_refs),
            breakdown=json.dumps(scan.breakdown, ensure_ascii=False) if scan.breakdown else None,
        )
        self.db.add(row)
        self.db.commit()
        return self._census_dict(row)

    def latest_census(self) -> dict | None:
        row = self.db.query(MediaCensus).order_by(MediaCensus.id.desc()).first()
        return self._census_dict(row) if row else None

    def overview(self) -> dict:
        """看板用：最新一份报告 + 回收站统计 + 当前生效的两条风险阈值。"""
        entries = (
            self.db.query(MediaTrashEntry)
            .filter(MediaTrashEntry.state == MediaTrashEntry.STATE_TRASHED)
            .order_by(MediaTrashEntry.id.desc())
            .limit(50)
            .all()
        )
        files, total_bytes = (
            self.db.query(
                func.count(MediaTrashEntry.id),
                func.coalesce(func.sum(MediaTrashEntry.bytes), 0),
            )
            .filter(MediaTrashEntry.state == MediaTrashEntry.STATE_TRASHED)
            .one()
        )
        return {
            "census": self.latest_census(),
            "trash": {
                "files": int(files or 0),
                "bytes": int(total_bytes or 0),
                "unmanaged": len(self.unmanaged_trash_files()),
                "min_age_minutes": self._cfg_int("media_trash_min_age_minutes", 60),
                "retain_days": self._cfg_int("media_trash_retain_days", 30),
                "items": [
                    {
                        "id": e.id,
                        "rel_path": e.rel_path,
                        "bucket": e.bucket,
                        "bytes": e.bytes,
                        "batch": e.batch,
                        "created_at": e.create_time.strftime("%Y-%m-%d %H:%M")
                        if e.create_time
                        else "",
                        "restore_until": e.restore_until.strftime("%Y-%m-%d")
                        if e.restore_until
                        else "",
                        "actor_name": e.actor_name,
                    }
                    for e in entries
                ],
            },
        }

    # ---------- 回收站记账对账（防"账被抹掉、文件成隐形库存"） ----------

    def trash_files_on_disk(self) -> list[tuple[str, str, int]]:
        """走一遍 `uploads/.trash/`，返回 [(批次, 相对路径, 字节)] —— **磁盘事实**。"""
        root = self.trash_root()
        out: list[tuple[str, str, int]] = []
        if not os.path.isdir(root):
            return out
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                inner = _norm(os.path.relpath(full, root))  # <批次>/<原相对路径>
                parts = inner.split("/", 1)
                if len(parts) != 2:
                    continue
                out.append((parts[0], parts[1], _file_size(full)))
        out.sort()
        return out

    def unmanaged_trash_files(self) -> list[tuple[str, str, int]]:
        """回收站里**没有记账**的文件（磁盘有、DB 无）。"""
        recorded = {
            (e.batch, e.rel_path)
            for e in self.db.query(MediaTrashEntry)
            .filter(MediaTrashEntry.state == MediaTrashEntry.STATE_TRASHED)
            .all()
        }
        return [row for row in self.trash_files_on_disk() if (row[0], row[1]) not in recorded]

    def reconcile_trash(self) -> dict:
        """把回收站里"没记账"的文件重新纳入账内（**只补账/搬回，绝不删除**）。

        为什么需要（2026-09-23 实修）：演示库清场重建（`check_demo_baseline` 会走 pytest 清业务表）
        会把 `media_trash_entries` 一并清空，而 `uploads/.trash/` 是磁盘事实——于是**文件还在、账没了**：
        界面上看不见、也不会被到期清除，成了隐形库存（当日实测：19:29 的一次清理搬走 3452 张，
        随后清场重建即出现此形态）。每日盘点会调本方法自愈。

        逐条处置（口径与五道防线一致）：
          - 该路径**正被引用**且 uploads 里已无此文件 → **搬回原位**（救回），计 `rescued`
          - 正被引用但 uploads 里也有一份 → 那是同内容重复件，**只报不动**，计 `conflicted`
          - 无人引用 → 补一条记账（保留期按配置重新起算），此后照常可还原 / 可清空，计 `adopted`
        """
        referenced = collect_referenced(self.db)
        uploads_root = self._root()
        retain_days = self._cfg_int("media_trash_retain_days", 30)
        now = datetime.now()
        adopted = rescued = conflicted = 0
        for batch, rel, size in self.unmanaged_trash_files():
            if rel in referenced:
                src = os.path.join(self.trash_root(), batch, rel)
                dst = os.path.join(uploads_root, rel)
                if os.path.isfile(src) and not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    rescued += 1
                else:
                    conflicted += 1
                continue
            self.db.add(
                MediaTrashEntry(
                    rel_path=rel,
                    bucket=rel.split("/", 1)[0] + "/",
                    bytes=size,
                    batch=batch,
                    state=MediaTrashEntry.STATE_TRASHED,
                    actor_id=None,
                    actor_name="系统·对账接管",
                    restore_until=now + timedelta(days=retain_days),
                )
            )
            adopted += 1
        if adopted or rescued:
            self.db.commit()
        return {"adopted": adopted, "rescued": rescued, "conflicted": conflicted}

    # ---------- 移入回收站 ----------

    def _reject_reason(
        self,
        rel: str,
        *,
        root: str,
        referenced: set[str],
        min_age_minutes: int,
        now: datetime,
    ) -> str | None:
        """八道闸门（**顺序即优先级**）：返回拒绝原因，None = 允许清理。"""
        abs_path = os.path.abspath(os.path.join(root, rel))
        if not rel or not abs_path.startswith(root + os.sep):
            return "路径非法（不在 uploads 内）"
        if is_trash_path(rel):
            return "已在回收站目录内"
        if rel in referenced:
            return "正在使用（DB 有引用，含软删记录）"
        if is_protected(rel):
            return "保护目录（人工上传，不可再生）"
        if not is_regenerable(rel):
            return "不在可再生白名单内（保守不动）"
        if not os.path.isfile(abs_path):
            return "文件不存在"
        already = (
            self.db.query(MediaTrashEntry)
            .filter(
                MediaTrashEntry.rel_path == rel,
                MediaTrashEntry.state == MediaTrashEntry.STATE_TRASHED,
            )
            .first()
        )
        if already:
            return "已在回收站（可还原）"
        age_minutes = (
            now - datetime.fromtimestamp(os.path.getmtime(abs_path))
        ).total_seconds() / 60
        if age_minutes < min_age_minutes:
            return f"生成不足 {min_age_minutes} 分钟（防上传竞态，稍后再试）"
        return None

    def trash(self, paths: list[str] | None, admin, reason: str, all_orphans: bool = False) -> dict:
        """把指定（或全部）孤儿**移动**到 `uploads/.trash/<批次>/`（不物理删除）。

        `all_orphans=True` = 清理当前盘点出的全部孤儿（运营按钮的主路径）；否则只清理 `paths`
        （逐条仍过全部闸门——**前端说什么不算数，以本次复算为准**）。
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("请填写清理原因（审计留痕）")
        root = self._root()
        now = datetime.now()
        min_age = self._cfg_int("media_trash_min_age_minutes", 60)
        retain_days = self._cfg_int("media_trash_retain_days", 30)
        referenced = collect_referenced(self.db)

        considered = (
            [p for p in self.scan().orphan_paths]
            if all_orphans
            else [_norm(p) for p in (paths or [])]
        )
        if not considered:
            raise ValidationError("没有可清理的文件（当前无孤儿图，或未选择任何文件）")

        batch = now.strftime("%Y%m%d-%H%M%S")
        moved: list[str] = []
        moved_bytes = 0
        skipped: list[dict] = []
        for rel in considered:
            reject = self._reject_reason(
                rel,
                root=root,
                referenced=referenced,
                min_age_minutes=min_age,
                now=now,
            )
            if reject:
                skipped.append({"path": rel, "reason": reject})
                continue
            src = os.path.join(root, rel)
            dst = os.path.join(self.trash_root(), batch, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            size = _file_size(src)
            try:
                shutil.move(src, dst)
            except OSError as exc:  # 移动失败不算业务失败：留着下次再清
                skipped.append({"path": rel, "reason": f"移动失败：{exc}"})
                continue
            self.db.add(
                MediaTrashEntry(
                    rel_path=rel,
                    bucket=rel.split("/", 1)[0] + "/",
                    bytes=size,
                    batch=batch,
                    state=MediaTrashEntry.STATE_TRASHED,
                    actor_id=getattr(admin, "id", None),
                    actor_name=(
                        getattr(admin, "display_name", "") or getattr(admin, "username", "")
                    ),
                    restore_until=now + timedelta(days=retain_days),
                )
            )
            moved.append(rel)
            moved_bytes += size

        if moved:
            from backend.domain.catalog.audit_events import publish_audit

            publish_audit(
                self.db,
                admin=admin,
                action="media.trash",
                target_type="media",
                target_id=batch,
                detail={
                    "files": len(moved),
                    "bytes": moved_bytes,
                    "skipped": len(skipped),
                    "paths": moved[:50],  # 审计行不留完整清单（可能上百条），前 50 条足够定位
                    "retain_days": retain_days,
                },
                reason=reason,
            )
        self.db.commit()
        return {
            "batch": batch,
            "moved": len(moved),
            "moved_bytes": moved_bytes,
            "skipped": skipped,
            "census": self.record_census(MediaCensus.TRIGGER_AFTER_TRASH),
        }

    # ---------- 还原 ----------

    def restore(self, entry_id: int, admin, reason: str = "") -> dict:
        """把回收站条目移回原位（原位置已被占用时**拒绝**，不覆盖）。"""
        entry = self.db.get(MediaTrashEntry, entry_id)
        if not entry or entry.state != MediaTrashEntry.STATE_TRASHED:
            raise NotFoundError("回收站条目不存在或已处理")
        src = os.path.join(self.trash_root(), entry.batch, entry.rel_path)
        dst = os.path.join(self._root(), entry.rel_path)
        if not os.path.isfile(src):
            raise NotFoundError("回收站里的文件已不存在")
        if os.path.exists(dst):
            raise ValidationError("原位置已有同名文件，请先处理后再还原")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        entry.state = MediaTrashEntry.STATE_RESTORED
        entry.restored_at = datetime.now()
        entry.restored_by = getattr(admin, "id", None)

        from backend.domain.catalog.audit_events import publish_audit

        publish_audit(
            self.db,
            admin=admin,
            action="media.restore",
            target_type="media",
            target_id=str(entry.id),
            detail={"path": entry.rel_path, "bytes": entry.bytes},
            reason=(reason or "").strip() or "从回收站还原",
        )
        self.db.commit()
        return {
            "rel_path": entry.rel_path,
            "census": self.record_census(MediaCensus.TRIGGER_AFTER_RESTORE),
        }

    # ---------- 解除回收站（到期清除 / 清空，共用同一套复检） ----------

    def _release_entries(self, rows: list[MediaTrashEntry], now: datetime) -> dict:
        """逐条复检后处置：**仍被引用 → 还原回原位；无人引用 → 物理删除**。

        这是"不会删掉正在使用的图片"的最后一道闸（docs/15 §22.2 第 5/6 道）：
        文件在回收站里期间若被重新引用（例：某本书重新上传了同一张图），
        这里会把它**请回原位**——到期清除与"清空回收站"共用本函数，两条路一道闸都不少。
        """
        referenced = collect_referenced(self.db)
        root = self._root()
        purged = restored = purged_bytes = 0
        restored_paths: list[str] = []
        for entry in rows:
            src = os.path.join(self.trash_root(), entry.batch, entry.rel_path)
            if entry.rel_path in referenced:
                dst = os.path.join(root, entry.rel_path)
                if os.path.isfile(src) and not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                entry.state = MediaTrashEntry.STATE_RESTORED
                entry.restored_at = now
                restored += 1
                restored_paths.append(entry.rel_path)
                continue
            if os.path.isfile(src):
                purged_bytes += _file_size(src)
                try:
                    os.remove(src)
                except OSError:
                    continue  # 删不掉留着，下次再来
            entry.state = MediaTrashEntry.STATE_PURGED
            purged += 1
        return {
            "purged": purged,
            "restored": restored,
            "purged_bytes": purged_bytes,
            "restored_paths": restored_paths,
        }

    def purge_expired(self, now: datetime | None = None) -> dict:
        """到期清除（每日任务调用；满 `media_trash_retain_days` 天）。"""
        now = now or datetime.now()
        rows = (
            self.db.query(MediaTrashEntry)
            .filter(
                MediaTrashEntry.state == MediaTrashEntry.STATE_TRASHED,
                MediaTrashEntry.restore_until.isnot(None),
                MediaTrashEntry.restore_until <= now,
            )
            .all()
        )
        outcome = self._release_entries(rows, now)
        if rows:
            self.db.commit()
        return {
            "purged": outcome["purged"],
            "restored": outcome["restored"],
            "purged_bytes": outcome["purged_bytes"],
        }

    def empty_trash(self, admin, reason: str) -> dict:
        """清空回收站（**仅超管**）：跳过 30 天等待期，**复检闸门照跑**（docs/15 §22.2 第 6 道）。

        语义是"提前到期"，不是"绕过防线"——被重新引用的条目仍会被救回原位并如实报给用户。
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("请填写清空原因（审计留痕）")
        now = datetime.now()
        rows = (
            self.db.query(MediaTrashEntry)
            .filter(MediaTrashEntry.state == MediaTrashEntry.STATE_TRASHED)
            .all()
        )
        if not rows:
            raise ValidationError("回收站已经是空的")
        outcome = self._release_entries(rows, now)

        from backend.domain.catalog.audit_events import publish_audit

        publish_audit(
            self.db,
            admin=admin,
            action="media.purge",
            target_type="media",
            target_id=now.strftime("%Y%m%d-%H%M%S"),
            detail={
                "purged": outcome["purged"],
                "purged_bytes": outcome["purged_bytes"],
                "restored": outcome["restored"],
                # 被复检救回并还原的条目要留痕：这是"闸门真的拦下了东西"的证据
                "restored_paths": outcome["restored_paths"][:50],
                "total": len(rows),
            },
            reason=reason,
        )
        self.db.commit()
        return {
            "purged": outcome["purged"],
            "purged_bytes": outcome["purged_bytes"],
            "restored": outcome["restored"],
            "census": self.record_census(MediaCensus.TRIGGER_AFTER_TRASH),
        }
