# backend/domain/identity/observation_service.py — 观察期评估报告（WM10，FEAT-066）
from __future__ import annotations

import json
import logging
import os

from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_OTHER_EVALUATION_UPLOADED, NotificationService
from backend.config import get_settings
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, ObservationReport

logger = logging.getLogger(__name__)


class ObservationReportService:
    def __init__(self, db: Session):
        self.db = db

    def upload_for_admin(self, admin, child_id: int, files: list, remark: str | None) -> dict:
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        return self.upload(admin, child, files, remark)

    def upload(self, admin, child: Child, files: list, remark: str | None) -> dict:
        if not files:
            raise ValidationError("请至少上传一张图片")
        if len(files) > 9:
            raise ValidationError("最多上传 9 张图片")
        from backend.common.file_storage import read_image_policy, save_observation_image

        policy = read_image_policy(self.db, "doc")
        # P2-26（2026-10-17 第六十九批·大审查）：**先全量校验、再统一落盘**。
        # 原先是"边校验边落盘"——第 3 张扩展名不合法时前 2 张已写进 observation/，
        # 而该目录在 PROTECTED_PREFIXES 里（媒体体检只计 protected_total、清理白名单不含它）
        # → **永久孤儿文件**，每次误传多图即复现、无任何清理路径。
        exts: list[str] = []
        for f in files:
            ext = os.path.splitext(f.filename or "")[1].lower()
            if ext not in (".png", ".jpg", ".jpeg"):
                raise ValidationError(f"仅支持 PNG/JPG 图片（{f.filename}）")
            exts.append(ext)
        paths: list[str] = []
        try:
            for f, ext in zip(files, exts, strict=True):
                paths.append(save_observation_image(child.id, f.file.read(), ext, policy))
        except Exception:
            # 落盘途中仍可能出错（磁盘满 / 解码失败 / 单图超限）——回收本次已写文件。
            # 内容是**内容寻址**的（同名=同图），可能已被历史报告引用（observation/ 不可再生），
            # 故只回收"无任何报告行引用"的那些（保守方向：宁可留孤儿，不能删活引用）。
            self._cleanup_unreferenced(paths)
            raise
        report = ObservationReport(
            child_id=child.id,
            images=json.dumps(paths, ensure_ascii=False),
            remark=remark,
            uploaded_by=admin.id,
        )
        self.db.add(report)
        self.db.flush()  # 先落库拿 id（下面拿它当去重键：报告 id 是"不会重来的事实"）
        # WM11：评估报告上传通知家长
        # 第四轮大审查 P2-14（2026-10-17）：此前不传 dedup_key（默认 "1"）+ ref_id=child_id
        # → **同一孩子终身只发第一条上传通知**（第 2 期起家长收不到）；P1 #2 家族漏网的第 4 处。
        NotificationService(self.db).send(
            parent_id=child.parent_id,
            scene=SCENE_OTHER_EVALUATION_UPLOADED,
            title="评估报告已上传",
            content=f"孩子的评估报告已更新（{len(paths)} 张图片），可在「我的 → 评估报告」查看。",
            category=Notification.CATEGORY_OTHER,
            child_id=child.id,
            ref_type="child",
            ref_id=str(child.id),
            dedup_key=str(report.id),
        )
        publish_audit(
            self.db,
            admin=admin,
            action="observation.report_upload",
            target_type="child",
            target_id=str(child.id),
            detail={"images": len(paths)},
            reason="评估报告上传",
        )
        self.db.commit()
        return {"id": report.id, "images": paths}

    def _cleanup_unreferenced(self, paths: list[str]) -> None:
        """回收本次上传中途失败时已落盘、且**无任何报告行引用**的图片（P2-26）。

        判据保守：`observation_reports.images` 里出现过该相对路径的**一段子串**即视为
        "被引用"（宁可留下孤儿，也绝不删掉其它报告正在用的图）；路径必须先过
        `media_paths.is_within` 才允许删（同前缀兄弟目录不放行——与下发端同一口径）。
        """
        if not paths:
            return
        from backend.common.media_paths import is_within
        from backend.common.sql_utils import escape_like

        root = os.path.abspath(get_settings().UPLOADS_DIR)
        keep: set[str] = set()
        for rel in paths:
            like = f"%{escape_like(rel)}%"
            if (
                self.db.query(ObservationReport.id)
                .filter(ObservationReport.images.like(like, escape="\\"))
                .first()
            ):
                keep.add(rel)
        for rel in paths:
            if rel in keep:
                continue
            full = os.path.abspath(os.path.join(root, rel))
            if not is_within(root, full) or not os.path.isfile(full):
                continue
            try:
                os.remove(full)
            except OSError as exc:  # 清理失败不回滚原异常——如实记日志
                logger.warning("评估报告孤儿图清理失败：%s（%s）", rel, exc)

    def list_for_child(self, child_id: int) -> list[dict]:
        rows = (
            self.db.query(ObservationReport)
            .filter(ObservationReport.child_id == child_id, ObservationReport.is_deleted == 0)
            .order_by(ObservationReport.id.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "child_id": r.child_id,
                "remark": r.remark,
                "images": json.loads(r.images or "[]"),
                "created_at": str(r.created_at),
            }
            for r in rows
        ]

    def image_owned_by(self, parent_id: int, stored_path: str, path_fragment: str) -> bool:
        """C-12/T25（P0-F1 同族第五案）：报告图片归属校验。

        images LIKE 候选过滤 + JSON 数组精确匹配完整存储路径（防子串误命中），
        再校验报告所属孩子的 parent_id。查无归属返回 False——端点一律 404，
        不区分"不存在"与"无权"（防枚举探测）。"""
        reports = (
            self.db.query(ObservationReport, Child.parent_id)
            .join(Child, ObservationReport.child_id == Child.id)
            .filter(
                ObservationReport.images.contains(path_fragment),
                ObservationReport.is_deleted == 0,
            )
            .all()
        )
        return any(
            stored_path in json.loads(report.images or "[]") and pid == parent_id
            for report, pid in reports
        )
