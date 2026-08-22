"""RBAC 种子数据 — 角色、权限、角色权限映射、admin 数据迁移

幂等：重复执行不报错。
"""

import logging

from sqlalchemy.orm import Session

from backend.database import get_session
from backend.domain.admin.models import Admin, SystemConfig
from backend.domain.admin.rbac_models import Permission, Role, RolePermission

logger = logging.getLogger(__name__)

# ── 角色定义 ──
ROLES = [
    {"code": "super_admin", "name": "超级管理员", "is_system": True, "sort_order": 0},
    {"code": "staff", "name": "运营人员", "is_system": True, "sort_order": 1},
    {"code": "teacher", "name": "教师", "is_system": True, "sort_order": 2},
]

# ── 权限定义（136 个）──
PERMISSIONS = [
    # dashboard
    {"code": "dashboard.view", "name": "数据概览", "group_name": "dashboard"},
    # user
    {"code": "user.list", "name": "用户列表", "group_name": "user"},
    {"code": "user.view", "name": "用户详情", "group_name": "user"},
    {"code": "user.create", "name": "创建用户", "group_name": "user"},
    {"code": "user.edit", "name": "编辑用户", "group_name": "user"},
    {"code": "user.delete", "name": "删除用户", "group_name": "user"},
    {"code": "user.export", "name": "导出用户", "group_name": "user"},
    # child
    {"code": "child.list", "name": "孩子列表", "group_name": "child"},
    {"code": "child.view", "name": "孩子详情", "group_name": "child"},
    {"code": "child.create", "name": "添加孩子", "group_name": "child"},
    {"code": "child.edit", "name": "编辑孩子", "group_name": "child"},
    {"code": "child.delete", "name": "删除孩子", "group_name": "child"},
    # order
    {"code": "order.list", "name": "订单列表", "group_name": "order"},
    {"code": "order.view", "name": "订单详情", "group_name": "order"},
    {"code": "order.create", "name": "创建订单", "group_name": "order"},
    {"code": "order.edit", "name": "修改订单", "group_name": "order"},
    {"code": "order.delete", "name": "删除订单", "group_name": "order"},
    {"code": "order.mark_paid", "name": "标记已付", "group_name": "order"},
    {"code": "order.refund", "name": "发起退款", "group_name": "order"},
    {"code": "order.export", "name": "导出订单", "group_name": "order"},
    # book
    {"code": "book.list", "name": "图书列表", "group_name": "book"},
    {"code": "book.view", "name": "图书详情", "group_name": "book"},
    {"code": "book.create", "name": "添加图书", "group_name": "book"},
    {"code": "book.edit", "name": "编辑图书", "group_name": "book"},
    {"code": "book.delete", "name": "删除图书", "group_name": "book"},
    {"code": "book.import", "name": "批量导入", "group_name": "book"},
    {"code": "book.export", "name": "导出图书", "group_name": "book"},
    # bookcopy
    {"code": "bookcopy.list", "name": "馆藏列表", "group_name": "bookcopy"},
    {"code": "bookcopy.create", "name": "生成馆藏", "group_name": "bookcopy"},
    {"code": "bookcopy.edit", "name": "编辑馆藏页", "group_name": "bookcopy"},
    # upload
    {"code": "upload.manage", "name": "文件上传", "group_name": "upload"},
    # borrow
    {"code": "borrow.list", "name": "借阅记录", "group_name": "borrow"},
    {"code": "borrow.create", "name": "借出", "group_name": "borrow"},
    {"code": "borrow.return", "name": "归还", "group_name": "borrow"},
    {"code": "borrow.overdue", "name": "逾期提醒", "group_name": "borrow"},
    {"code": "borrow.fine_clear", "name": "清除罚款", "group_name": "borrow"},
    {"code": "borrow.mark_lost", "name": "标记图书丢失", "group_name": "borrow"},
    # deposit
    {"code": "deposit.list", "name": "押金列表", "group_name": "deposit"},
    {"code": "deposit.pay", "name": "代缴押金", "group_name": "deposit"},
    {"code": "deposit.refund", "name": "退款操作", "group_name": "deposit"},
    {"code": "deposit.deduct", "name": "扣除押金", "group_name": "deposit"},
    # reservation
    {"code": "reservation.list", "name": "预约列表", "group_name": "reservation"},
    {"code": "reservation.create", "name": "创建预约", "group_name": "reservation"},
    {"code": "reservation.fulfill", "name": "确认取书", "group_name": "reservation"},
    {"code": "reservation.cancel", "name": "取消预约", "group_name": "reservation"},
    # submission
    {"code": "submission.list", "name": "提交列表", "group_name": "submission"},
    {"code": "submission.view", "name": "提交详情", "group_name": "submission"},
    {"code": "submission.approve", "name": "审核通过", "group_name": "submission"},
    {"code": "submission.reject", "name": "审核打回", "group_name": "submission"},
    # activity
    {"code": "activity.list", "name": "活动列表", "group_name": "activity"},
    {"code": "activity.view", "name": "活动详情", "group_name": "activity"},
    {"code": "activity.create", "name": "创建活动", "group_name": "activity"},
    {"code": "activity.edit", "name": "编辑活动", "group_name": "activity"},
    {"code": "activity.delete", "name": "删除活动", "group_name": "activity"},
    {"code": "activity.cancel", "name": "取消活动", "group_name": "activity"},
    {"code": "activity.enrollment", "name": "报名列表", "group_name": "activity"},
    {"code": "activity.checkin", "name": "签到", "group_name": "activity"},
    # assessment
    {"code": "assessment.list", "name": "评估列表", "group_name": "assessment"},
    {"code": "assessment.view", "name": "评估详情", "group_name": "assessment"},
    {"code": "assessment.create", "name": "创建评估", "group_name": "assessment"},
    {"code": "assessment.edit", "name": "编辑评估", "group_name": "assessment"},
    {"code": "assessment.delete", "name": "删除评估", "group_name": "assessment"},
    # evaluation
    {"code": "evaluation.create", "name": "创建AR测评", "group_name": "evaluation"},
    {"code": "evaluation.list", "name": "AR测评列表", "group_name": "evaluation"},
    {"code": "evaluation.view", "name": "AR测评详情", "group_name": "evaluation"},
    # quiz
    {"code": "quiz.list", "name": "测验列表", "group_name": "quiz"},
    {"code": "quiz.view", "name": "测验详情", "group_name": "quiz"},
    # question
    {"code": "question.list", "name": "题库列表", "group_name": "question"},
    {"code": "question.create", "name": "创建题目", "group_name": "question"},
    {"code": "question.edit", "name": "编辑题目", "group_name": "question"},
    {"code": "question.delete", "name": "删除题目", "group_name": "question"},
    {"code": "question.import", "name": "批量导入", "group_name": "question"},
    # level
    {"code": "level.list", "name": "级别列表", "group_name": "level"},
    {"code": "level.create", "name": "创建级别", "group_name": "level"},
    {"code": "level.edit", "name": "编辑级别", "group_name": "level"},
    {"code": "level.delete", "name": "删除级别", "group_name": "level"},
    # achievement
    {"code": "achievement.list", "name": "成就列表", "group_name": "achievement"},
    {"code": "achievement.create", "name": "创建成就", "group_name": "achievement"},
    {"code": "achievement.edit", "name": "编辑成就", "group_name": "achievement"},
    {"code": "achievement.delete", "name": "删除成就", "group_name": "achievement"},
    # certificate
    {"code": "certificate.list", "name": "证书列表", "group_name": "certificate"},
    {"code": "certificate.regenerate", "name": "重新生成", "group_name": "certificate"},
    {"code": "certificate.delete", "name": "删除证书", "group_name": "certificate"},
    # report
    {"code": "report.list", "name": "报告列表", "group_name": "report"},
    {"code": "report.view", "name": "报告详情", "group_name": "report"},
    {"code": "report.generate", "name": "生成报告", "group_name": "report"},
    {"code": "report.comment", "name": "评语", "group_name": "report"},
    {"code": "report.reading_data", "name": "阅读数据", "group_name": "report"},
    # refund
    {"code": "refund.list", "name": "退款列表", "group_name": "refund"},
    {"code": "refund.audit", "name": "退款审核", "group_name": "refund"},
    # benefit_transfer
    {
        "code": "benefit_transfer.list",
        "name": "权益转移列表",
        "group_name": "benefit_transfer",
    },
    {
        "code": "benefit_transfer.review",
        "name": "权益转移审核",
        "group_name": "benefit_transfer",
    },
    # teacher
    {"code": "teacher.list", "name": "老师列表", "group_name": "teacher"},
    {"code": "teacher.view", "name": "老师详情", "group_name": "teacher"},
    {"code": "teacher.create", "name": "添加老师", "group_name": "teacher"},
    {"code": "teacher.edit", "name": "编辑老师", "group_name": "teacher"},
    {"code": "teacher.delete", "name": "删除老师", "group_name": "teacher"},
    {"code": "teacher.assign", "name": "分配学生", "group_name": "teacher"},
    {"code": "teacher.schedule", "name": "排课管理", "group_name": "teacher"},
    {"code": "teacher.workbench", "name": "老师工作台", "group_name": "teacher"},
    # venue
    {"code": "venue.list", "name": "场馆列表", "group_name": "venue"},
    {"code": "venue.create", "name": "创建场馆", "group_name": "venue"},
    {"code": "venue.edit", "name": "编辑场馆", "group_name": "venue"},
    {"code": "venue.delete", "name": "删除场馆", "group_name": "venue"},
    # message
    {"code": "message.list", "name": "消息列表", "group_name": "message"},
    {"code": "message.send", "name": "发送消息", "group_name": "message"},
    {"code": "message.delete", "name": "删除消息", "group_name": "message"},
    # config
    {"code": "config.view", "name": "配置查看", "group_name": "config"},
    {"code": "config.edit", "name": "配置编辑", "group_name": "config"},
    # admin
    {"code": "admin.list", "name": "管理员列表", "group_name": "admin"},
    {"code": "admin.create", "name": "创建管理员", "group_name": "admin"},
    {"code": "admin.edit", "name": "编辑管理员", "group_name": "admin"},
    {"code": "admin.delete", "name": "删除管理员", "group_name": "admin"},
    {"code": "admin.password", "name": "改密码", "group_name": "admin"},
    # role
    {"code": "role.list", "name": "角色列表", "group_name": "role"},
    {"code": "role.edit", "name": "角色权限", "group_name": "role"},
    # log
    {"code": "log.list", "name": "操作日志", "group_name": "log"},
    # recycle
    {"code": "recycle.list", "name": "回收站", "group_name": "recycle"},
    {"code": "recycle.restore", "name": "恢复", "group_name": "recycle"},
    {"code": "recycle.delete", "name": "彻底删除", "group_name": "recycle"},
    # dictionary
    {"code": "dictionary.list", "name": "词库列表", "group_name": "dictionary"},
    {"code": "dictionary.edit", "name": "编辑词库", "group_name": "dictionary"},
    {"code": "dictionary.create", "name": "创建单词", "group_name": "dictionary"},
    {"code": "dictionary.delete", "name": "删除单词", "group_name": "dictionary"},
    # book_damage
    {"code": "book_damage.create", "name": "登记损坏", "group_name": "book_damage"},
    {"code": "book_damage.list", "name": "损坏列表", "group_name": "book_damage"},
    {"code": "book_damage.appeal", "name": "申诉处理", "group_name": "book_damage"},
    {"code": "book_damage.review", "name": "审核定责", "group_name": "book_damage"},
    # content
    {"code": "content.list", "name": "内容列表", "group_name": "content"},
    {"code": "content.edit", "name": "编辑内容", "group_name": "content"},
    {"code": "content.create", "name": "创建内容", "group_name": "content"},
    {"code": "content.delete", "name": "删除内容", "group_name": "content"},
    # parent_course_time
    {
        "code": "parent_course_time.list",
        "name": "时间段列表",
        "group_name": "parent_course_time",
    },
    {
        "code": "parent_course_time.create",
        "name": "创建时间段",
        "group_name": "parent_course_time",
    },
    {
        "code": "parent_course_time.edit",
        "name": "编辑时间段",
        "group_name": "parent_course_time",
    },
    {
        "code": "parent_course_time.delete",
        "name": "删除时间段",
        "group_name": "parent_course_time",
    },
]

# ── 角色权限映射 ──
# super_admin: 全部权限（在代码中特殊处理）
STAFF_PERMS = [
    "dashboard.view",
    "user.list",
    "user.view",
    "user.create",
    "user.edit",
    "user.export",
    "child.list",
    "child.view",
    "child.create",
    "child.edit",
    "order.list",
    "order.view",
    "order.create",
    "order.edit",
    "book.list",
    "book.create",
    "book.edit",
    "book.import",
    "book.export",
    "bookcopy.list",
    "bookcopy.create",
    "bookcopy.edit",
    "upload.manage",
    "borrow.list",
    "borrow.create",
    "borrow.return",
    "borrow.fine_clear",
    "borrow.mark_lost",
    "deposit.list",
    "deposit.pay",
    "reservation.list",
    "reservation.create",
    "reservation.fulfill",
    "reservation.cancel",
    "submission.list",
    "submission.approve",
    "activity.list",
    "activity.view",
    "activity.create",
    "activity.edit",
    "activity.cancel",
    "activity.enrollment",
    "activity.checkin",
    "assessment.list",
    "assessment.view",
    "assessment.create",
    "assessment.edit",
    "assessment.delete",
    "quiz.list",
    "question.list",
    "question.create",
    "question.edit",
    "question.import",
    "level.list",
    "level.create",
    "achievement.list",
    "achievement.create",
    "achievement.edit",
    "certificate.list",
    "certificate.regenerate",
    "report.list",
    "report.generate",
    "report.comment",
    "report.reading_data",
    "refund.list",
    "teacher.list",
    "teacher.view",
    "teacher.create",
    "teacher.edit",
    "teacher.assign",
    "teacher.schedule",
    "venue.list",
    "venue.create",
    "venue.edit",
    "message.list",
    "message.send",
    "log.list",
    "recycle.list",
    "recycle.restore",
    "dictionary.list",
    "dictionary.edit",
    "dictionary.create",
    "dictionary.delete",
    "content.list",
    "content.edit",
    "parent_course_time.list",
    "parent_course_time.create",
    "parent_course_time.edit",
    "parent_course_time.delete",
    "benefit_transfer.list",
    "book_damage.create",
    "book_damage.list",
    "book_damage.appeal",
    "book_damage.review",
    "content.create",
    "content.delete",
]

TEACHER_PERMS = [
    "dashboard.view",
    "child.list",
    "child.view",
    "book.list",
    "borrow.list",
    "borrow.mark_lost",
    "submission.list",
    "submission.approve",
    "activity.list",
    "activity.view",
    "activity.enrollment",
    "activity.checkin",
    "assessment.list",
    "assessment.view",
    "quiz.list",
    "question.list",
    "level.list",
    "achievement.list",
    "certificate.list",
    "report.list",
    "report.comment",
    "report.reading_data",
    "message.list",
    "teacher.workbench",
]

OLD_ROLE_MAP = {0: "super_admin", 1: "staff", 2: "teacher"}


def seed_roles(db: Session):
    """幂等插入角色"""
    for data in ROLES:
        existing = db.query(Role).filter(Role.code == data["code"]).first()
        if not existing:
            db.add(Role(**data))
    db.flush()


def seed_permissions(db: Session):
    """幂等插入权限"""
    for data in PERMISSIONS:
        existing = db.query(Permission).filter(Permission.code == data["code"]).first()
        if not existing:
            db.add(Permission(**data))
    db.flush()


def seed_role_permissions(db: Session):
    """幂等插入角色权限映射"""
    role_map = {r.code: r.id for r in db.query(Role).all()}

    # super_admin — 全部权限（直接从 PERMISSIONS 全量，不漏不缺）
    super_admin_perms = [p["code"] for p in PERMISSIONS]
    _batch_insert_role_perms(db, role_map["super_admin"], super_admin_perms)
    _batch_insert_role_perms(db, role_map["staff"], STAFF_PERMS)
    _batch_insert_role_perms(db, role_map["teacher"], TEACHER_PERMS)


def _batch_insert_role_perms(db: Session, role_id: int, perm_codes: list[str]):
    """幂等插入 + 清理已移除的权限码"""
    existing = {
        rp.permission_code
        for rp in db.query(RolePermission)
        .filter(
            RolePermission.role_id == role_id,
            RolePermission.is_deleted == 0,
        )
        .all()
    }
    # 新增
    new_codes = [c for c in perm_codes if c not in existing]
    for code in new_codes:
        db.add(RolePermission(role_id=role_id, permission_code=code))
    # 清理：列表已移除的权限码，软删除
    removed = existing - set(perm_codes)
    if removed:
        db.query(RolePermission).filter(
            RolePermission.role_id == role_id,
            RolePermission.permission_code.in_(removed),
            RolePermission.is_deleted == 0,
        ).update({"is_deleted": 1}, synchronize_session=False)


def seed_default_configs(db: Session):
    """幂等初始化系统配置默认值

    唯一来源: SystemConfig.DEFAULTS（backend/domain/admin/models.py:77）
    L3-042：自动覆盖全部 DEFAULTS 键（当前 67 项），统一 config_type，
    消除维护两份配置清单的风险。
    """
    for key, (value, typ, desc) in SystemConfig.DEFAULTS.items():
        existing = (
            db.query(SystemConfig)
            .filter(
                SystemConfig.config_key == key,
                SystemConfig.is_deleted == 0,
            )
            .first()
        )
        if not existing:
            db.add(
                SystemConfig(
                    config_key=key,
                    config_value=str(value),
                    config_type=typ,
                    description=desc,
                )
            )

    # 清理旧版 seeder 写入的死数据键名（observation_price→price_observation, etc.）
    dead_keys = ["observation_price", "official_member_price"]
    dead = (
        db.query(SystemConfig)
        .filter(
            SystemConfig.config_key.in_(dead_keys),
            SystemConfig.is_deleted == 0,
        )
        .all()
    )
    for c in dead:
        c.is_deleted = 1
        logger.info(
            "清理死数据配置键: %s (已被 %s 替代)",
            c.config_key,
            {
                "observation_price": "price_observation",
                "official_member_price": "price_official_member",
            }.get(c.config_key, "?"),
        )

    db.flush()


def migrate_admin_roles(db: Session):
    """将旧 admin.role (0/1/2) 迁移到新的 admin_role_id"""
    role_code_map = {r.code: r.id for r in db.query(Role).all()}
    admins = db.query(Admin).filter(Admin.admin_role_id.is_(None), Admin.is_deleted == 0).all()
    for admin in admins:
        if admin.role is not None:
            code = OLD_ROLE_MAP.get(admin.role, "staff")
            new_role_id = role_code_map.get(code)
            if new_role_id:
                admin.admin_role_id = new_role_id


def run():
    """主入口"""
    session_factory = get_session()
    db = session_factory()
    try:
        seed_roles(db)
        seed_permissions(db)
        seed_role_permissions(db)
        migrate_admin_roles(db)
        seed_default_configs(db)
        db.commit()
        # 统计
        role_count = db.query(Role).filter(Role.is_deleted == 0).count()
        perm_count = db.query(Permission).filter(Permission.is_deleted == 0).count()
        rp_count = db.query(RolePermission).filter(RolePermission.is_deleted == 0).count()
        migrated = db.query(Admin).filter(Admin.admin_role_id.isnot(None)).count()
        logger.info(
            f"RBAC 种子完成: roles={role_count}, permissions={perm_count}, role_permissions={rp_count}, admin_migrated={migrated}"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
