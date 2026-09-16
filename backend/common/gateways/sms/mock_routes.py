# backend/common/gateways/sms/mock_routes.py
"""Mock 短信辅助路由 — 仅在 MOCK_SMS=true 且 DEBUG=true 时注册

未接线（2026-09-16 复核）：**本路由未被注册进 app**（main.py 无 include_router），故 /mock/sms/code/{phone} 当前是死的。
保留原因：本地联调取验证码的真实需求仍在（用户裁决"留着"）；启用只需在 main.py 按开关注册。

提供测试用验证码读取接口，需 admin 鉴权，仅本地开发环境可见。
"""

from fastapi import APIRouter, Depends, HTTPException

from backend.common.gateways.sms.mock import MockSmsGateway
from backend.middleware.admin_auth import get_current_admin

mock_sms_router = APIRouter(prefix="/mock/sms", tags=["Mock-短信"])


@mock_sms_router.get("/code/{phone}")
def get_sms_code(phone: str, admin=Depends(get_current_admin)):
    """获取指定手机号的验证码（仅 Mock 模式，需 admin 鉴权）"""
    code = MockSmsGateway.get_code(phone)
    if code is None:
        raise HTTPException(404, f"手机号 {phone} 无有效验证码")
    return {"phone": phone, "code": code}
