# backend/domain/identity/observation_service.py — 观察期评估报告（WM10，FEAT-066）
from __future__ import annotations

import json
import os
import uuid

from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_OTHER_EVALUATION_UPLOADED, NotificationService
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, ObservationReport


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
        from backend.config import get_settings

        root = os.path.abspath(get_settings().UPLOADS_DIR)
        rel_dir = os.path.join("observation", f"child_{child.id}")
        out_dir = os.path.join(root, rel_dir)
        os.makedirs(out_dir, exist_ok=True)
        paths = []
        for f in files:
            ext = os.path.splitext(f.filename or "")[1].lower()
            if ext not in (".png", ".jpg", ".jpeg"):
                raise ValidationError(f"仅支持 PNG/JPG 图片（{f.filename}）")
            name = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(out_dir, name), "wb") as out:
                out.write(f.file.read())
            paths.append(os.path.join(rel_dir, name))
        report = ObservationReport(
            child_id=child.id,
            images=json.dumps(paths, ensure_ascii=False),
            remark=remark,
            uploaded_by=admin.id,
        )
        self.db.add(report)
        # WM11：评估报告上传通知家长
        NotificationService(self.db).send(
            parent_id=child.parent_id,
            scene=SCENE_OTHER_EVALUATION_UPLOADED,
            title="评估报告已上传",
            content=f"孩子的评估报告已更新（{len(paths)} 张图片），可在「我的 → 评估报告」查看。",
            category=Notification.CATEGORY_OTHER,
            child_id=child.id,
            ref_type="child",
            ref_id=str(child.id),
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
