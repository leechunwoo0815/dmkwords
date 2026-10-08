# backend/domain/identity/auth.py — 小程序家长鉴权统一（A-1/T6 下沉 20260903）
"""家长 token 签发/解析、当前家长依赖、孩子归属校验、家长登录。
从各域 miniapp_router 下沉（架构门禁扩 miniapp_router 后 Router 零违规）。

query-token 鉴权：音频/图片等组件无法携带 Authorization 头时用。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, Header
from sqlalchemy.exc import IntegrityError

from backend.common.exceptions import UnauthorizedError, ValidationError
from backend.config import get_settings
from backend.database import get_db
from backend.domain.identity.models import Child, Parent


def _parent_token(parent_id: int) -> str:
    import jwt as pyjwt

    payload = {
        "sub": str(parent_id),
        "type": "parent",
        "exp": datetime.now(UTC) + timedelta(days=30),
    }
    return pyjwt.encode(payload, get_settings().SECRET_KEY, algorithm="HS256")


def _parent_from_token(token: str, db) -> Parent:
    import jwt as pyjwt

    try:
        payload = pyjwt.decode(token, get_settings().SECRET_KEY, algorithms=["HS256"])
    except pyjwt.PyJWTError as e:
        raise UnauthorizedError("请先登录") from e
    if payload.get("type") != "parent":
        raise UnauthorizedError("无效的家长凭证")
    parent = (
        db.query(Parent).filter(Parent.id == int(payload["sub"]), Parent.is_deleted == 0).first()
    )
    if not parent:
        raise UnauthorizedError("账号不存在")
    return parent


def get_current_parent(authorization: str = Header(...), db=Depends(get_db)) -> tuple[Parent, Any]:
    """FastAPI 依赖：解析家长 token，返回 (Parent, Session)。"""
    token = authorization.replace("Bearer ", "")
    return _parent_from_token(token, db), db


def child_of_parent(db, parent_id: int, child_id: int) -> Child:
    """孩子归属校验（P0-F1 防越权）：查孩子且属该家长，否则 422。"""
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    return child


def children_payload(db, parent_id: int) -> list[dict]:
    """家长名下孩子的**唯一序列化入口**（登录与 `GET /api/miniapp/children` 共用）。

    为什么必须单源：小程序把这份列表缓存在本地，两个消费方（登录快照 / 刷新接口）
    若各写一份，字段迟早漂移。2026-09-16 用户报「点赞的头像跟我的页面的头像不匹配」——
    根因就是本地快照只此一份、且**缺 member_expire**（我的页"到期时间"行永远是空的，
    后台改了头像也不会同步）。改这里 = 两个消费方一起改。

    字段约定（前端消费点）：
      - id / name / english_name / avatar / level → 我的页头像、切换孩子、阅读圈头像框
      - member_status → 会员状态文案
      - member_start / member_expire → 我的页「到期时间」行（_expireLine 依赖）
    """
    children = (
        db.query(Child)
        .filter(Child.parent_id == parent_id, Child.is_deleted == 0)
        .order_by(Child.id)
        .all()
    )
    from backend.domain.growth.service import levels_map

    lv = levels_map(db, [c.id for c in children])
    return [
        {
            "id": c.id,
            "name": c.name,
            "english_name": c.english_name,
            "member_status": c.member_status,
            "member_start": str(c.member_start) if c.member_start else None,
            "member_expire": str(c.member_expire) if c.member_expire else None,
            "avatar": c.avatar,
            # fix34 R4：带等级（前端按等级映射头像框档位；批查一次，禁逐条）
            "level": lv.get(c.id, "A"),
            # 会员码（2026-09-21 任务包 A 批）：小程序「会员码」页出示给借阅台扫码。
            # 历史行为空时前端走"联系馆员"兜底文案，不要把 None 当码显示。
            "member_code": c.member_code,
        }
        for c in children
    ]


def _login_payload(db, parent: Parent) -> dict:
    """登录成功的统一载荷（三条通道共用：开发固定码 / 短信 / 微信）。"""
    return {
        "token": _parent_token(parent.id),
        "parent": {
            "id": parent.id,
            "name": parent.name,
            "phone": parent.phone,
            # WM12-C（审查 P0-3，用户裁定"线上支付只允许微信一键登录"）：
            # 前端据此隐藏支付入口——短信/固定码登录拿不到 openid，点了必然 422。
            "wechat_bound": bool((parent.wechat_openid or "").strip()),
        },
        "children": children_payload(db, parent.id),
    }


def authenticate_parent(db, phone: str, code: str) -> dict:
    """短信验证码登录（2026-10-08 接线）：校验通过 + 查家长 + 返回 token 与孩子列表。

    两条通道（互斥）：
      ① 开发固定码：`LOGIN_DEV_CODE` 非空且 code 与之相等 → 直接放行（供测试/演示；生产置空）；
      ② 短信验证码：表 `sms_codes` 的服务端校验（限流、一次性、失败次数上限）。
    生产 `LOGIN_DEV_CODE` 为空 ⇒ ① 恒不成立 ⇒ 任何 code 都必须过短信校验（fail-closed）。

    ⚠️ 微信 openid 是**主通道**（`login_by_wechat` / `bind_wechat`），本函数是兜底与"换手机号"用。
    """
    dev_code = get_settings().LOGIN_DEV_CODE
    if not (dev_code and code == dev_code):
        from backend.domain.identity.models import SmsCode
        from backend.domain.identity.sms_service import SmsCodeService

        SmsCodeService(db).verify(phone, code, SmsCode.PURPOSE_LOGIN)
    parent = db.query(Parent).filter(Parent.phone == phone, Parent.is_deleted == 0).first()
    if not parent:
        raise ValidationError("该手机号未注册（请到店建档）")
    return _login_payload(db, parent)


# ---------------- 微信 openid 登录（主通道） ----------------


def _wechat_openid(code: str) -> str:
    """code2session（异步集成 → 同步端点内跑一次事件循环）。"""
    import asyncio

    from backend.integrations.wechat.auth import WeChatAuth

    data = asyncio.run(WeChatAuth.code_to_session(code))
    openid = data.get("openid")
    if not openid:
        raise ValidationError("微信登录失败：未取到 openid")
    return openid


def make_bind_ticket(openid: str, ttl_minutes: int = 10) -> str:
    """首次登录的绑定凭证（JWT，type=bind）：只够用来绑一次手机号，不能当登录态使。"""
    import jwt as pyjwt

    payload = {
        "sub": openid,
        "type": "bind",
        "exp": datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    }
    return pyjwt.encode(payload, get_settings().SECRET_KEY, algorithm="HS256")


def parse_bind_ticket(ticket: str) -> str:
    import jwt as pyjwt

    try:
        payload = pyjwt.decode(ticket, get_settings().SECRET_KEY, algorithms=["HS256"])
    except pyjwt.PyJWTError as e:
        raise UnauthorizedError("绑定凭证无效或已过期，请重新登录") from e
    if payload.get("type") != "bind" or not payload.get("sub"):
        raise UnauthorizedError("绑定凭证无效")
    return str(payload["sub"])


def login_by_wechat(db, code: str) -> dict:
    """微信一键登录：openid 已绑家长 → 直接发登录态；未绑 → 返回 need_bind + 绑定凭证。"""
    from backend.domain.identity.sms_service import parent_by_openid

    openid = _wechat_openid(code)
    parent = parent_by_openid(db, openid)
    if parent:
        return _login_payload(db, parent)
    return {"need_bind": True, "bind_ticket": make_bind_ticket(openid)}


def bind_wechat(db, bind_ticket: str, phone: str, code: str) -> dict:
    """首次绑定：校验绑定凭证 + 短信验证码 + 手机号已在馆建档 → 落 openid 并发登录态。

    家长档案由馆员"到店建档"创建（phone 唯一）——家长端**不允许自助注册**，
    所以这里只做"认领"：手机号必须已存在。同一 openid 已绑别的家长 → 拒绝（防顶号）。
    """
    from backend.domain.identity.models import SmsCode
    from backend.domain.identity.sms_service import SmsCodeService, parent_by_openid

    openid = parse_bind_ticket(bind_ticket)
    SmsCodeService(db).verify(phone, code, SmsCode.PURPOSE_BIND)
    parent = db.query(Parent).filter(Parent.phone == phone, Parent.is_deleted == 0).first()
    if not parent:
        raise ValidationError("该手机号未注册（请到店建档）")
    if parent.wechat_openid and parent.wechat_openid != openid:
        raise ValidationError("该手机号已绑定其他微信，请联系馆员处理")
    occupied = parent_by_openid(db, openid)
    if occupied and occupied.id != parent.id:
        raise ValidationError("该微信已绑定其他手机号，请联系馆员处理")
    parent.wechat_openid = openid
    try:
        db.commit()
    except IntegrityError as exc:
        # WM12-C（审查 P2-13）：`parents.wechat_openid` 是唯一索引，两个请求同时绑同一微信时
        # 后到者原先会以 500 收场（堆栈暴露）；这里是业务冲突，回滚并转 422。
        db.rollback()
        raise ValidationError("该微信刚刚已被绑定，请重新登录或联系馆员处理") from exc
    return _login_payload(db, parent)
