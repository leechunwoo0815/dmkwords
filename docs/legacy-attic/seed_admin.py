# backend/seeds/seed_admin.py
"""创建管理员种子数据"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.database import Base, _get_engine, get_session
from backend.domain.admin.models import Admin
from backend.utils.password import hash_password

logger = logging.getLogger(__name__)


def seed():
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)
    db = get_session()()
    try:
        # 检查是否已有管理员
        existing = db.query(Admin).filter(Admin.username == "admin").first()
        if existing:
            logger.info(f"管理员已存在: id={existing.id}, username={existing.username}")
            return

        # F34：初始超管直接关联 super_admin 角色（admin_role_id）——
        # is_super_admin 只认 RBAC 角色（admin_rbac.py），依赖 seed_rbac
        # migrate_admin_roles 手动回填会把超管锁在门外且排查路径隐晦
        from backend.domain.admin.models import Role

        super_role = db.query(Role).filter(Role.code == "super_admin", Role.is_deleted == 0).first()
        admin = Admin(
            username="admin",
            name="超级管理员",
            role=0,  # ROLE_ADMIN（@deprecated，兼容旧逻辑）
            status=1,
            admin_role_id=super_role.id if super_role else None,
        )
        default_password = os.environ.get("ADMIN_PASSWORD")
        if not default_password:
            logger.info("❌ 必须设置 ADMIN_PASSWORD 环境变量才能创建管理员")
            logger.info(
                "   示例: ADMIN_PASSWORD=your-strong-password python -m backend.seeds.seed_admin"
            )
            return
        admin.password_hash = hash_password(default_password)
        db.add(admin)
        db.commit()
        logger.info(f"管理员创建成功: id={admin.id}, username=admin")
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
