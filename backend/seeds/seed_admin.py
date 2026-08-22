# backend/seeds/seed_admin.py — 后台账号种子（幂等）
"""用法：python -m backend.seeds.seed_admin

创建：admin/dmkwords123（超管）、staff01/dmkwords123（运营专员）。
已存在同用户名则跳过（幂等）。
"""

from backend.common.security import hash_password
from backend.database import get_session
from backend.domain.admin.models import AdminUser
from backend.domain.admin.repository import AdminUserRepository

SEED_ACCOUNTS = [
    {
        "username": "admin",
        "password": "dmkwords123",
        "display_name": "超级管理员",
        "role": AdminUser.ROLE_SUPER_ADMIN,
    },
    {
        "username": "staff01",
        "password": "dmkwords123",
        "display_name": "运营专员01",
        "role": AdminUser.ROLE_STAFF,
    },
]


def seed() -> list[str]:
    db = get_session()
    created: list[str] = []
    try:
        repo = AdminUserRepository(db)
        for account in SEED_ACCOUNTS:
            if repo.get_by_username(account["username"]):
                continue
            repo.create(
                AdminUser(
                    username=account["username"],
                    password_hash=hash_password(account["password"]),
                    display_name=account["display_name"],
                    role=account["role"],
                    status=AdminUser.STATUS_ACTIVE,
                )
            )
            created.append(account["username"])
        db.commit()
        return created
    finally:
        db.close()


if __name__ == "__main__":
    result = seed()
    print(f"账号种子完成，新建: {result or '无（均已存在）'}")
