# backend/common/gateways/sms/mock_routes.py
"""Mock 短信辅助路由 — 仅在 SMS_PROVIDER=mock 且 DEBUG=true 时注册

未接线（2026-09-16 复核）：**本路由未被注册进 app**（main.py 无 include_router），故 /mock/sms/code/{phone} 当前是死的。
保留原因：本地联调取验证码的真实需求仍在（用户裁决"留着"）；启用只需在 main.py 按开关注册。

P3-6（2026-10-18 第七十六批）：**路由不再抛 `HTTPException`**——那是分层违规（Router 只做参数/
权限装配，业务异常由 Service 抛、全局处理器映射状态码）。原先"未挂载所以架构关扫不到"，
一旦按开关启用就会带病上线；现改为 `NotFoundError`（全局处理器映射 404，行为不变）。
"""

from fastapi import APIRouter, Depends

from backend.common.exceptions import NotFoundError
from backend.common.gateways.sms.mock import MockSmsGateway
from backend.middleware.admin_auth import get_current_admin

mock_sms_router = APIRouter(prefix="/mock/sms", tags=["Mock-短信"])


@mock_sms_router.get("/code/{phone}")
def get_sms_code(phone: str, admin=Depends(get_current_admin)):
    """获取指定手机号的验证码（仅 Mock 模式，需 admin 鉴权）"""
    code = MockSmsGateway.get_code(phone)
    if code is None:
        # 业务异常（全局处理器 → 404）；Router 不得抛 HTTPException（宪法 §三）
        raise NotFoundError(f"手机号 {phone} 无有效验证码")
    return {"phone": phone, "code": code}
