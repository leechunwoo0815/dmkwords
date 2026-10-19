# tests/unit/test_audit8_a3_pay_anomaly_bound_20261019.py — 第八轮审查 A 批：P1-3 回调留痕有界
"""**缺陷（第八轮审查 P1-3，实测复现）**：`/api/pay/` 按设计**免限流**（微信会重试，限流会把回调
挡在门外 = 丢单），但验签失败/报文不可解析都会走 `_audit_anomaly` → **每次请求写 1 行审计 +
1 行管理待办**（`ref_id` 带报文指纹，换 body 即换键）→ 未认证攻击者可**无上限**填充两张表
（实测：5 次请求 → `audit_logs +5` / `admin_notifications +5`）。

**修复**：给**留痕本身**加进程内分钟预算（`ANOMALY_PER_MINUTE=60`），超出只进进程日志；
端点响应语义不变（401/400 照旧），微信重试不受影响。

**变异自证**：去掉 `_anomaly_budget_ok()` 判断（或把预算改到 >70）→ 第一条用例**红**。
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy import func

from backend.common.admin_notification_models import AdminNotification
from backend.common.system_models import AuditLog
from backend.database import get_session
from backend.domain.identity.payment_notify import ANOMALY_PER_MINUTE

SIG_HEADERS = {
    "Wechatpay-Signature": "not-a-real-signature",
    "Wechatpay-Timestamp": "1",
    "Wechatpay-Nonce": "n",
}


def _counts() -> tuple[int, int]:
    with get_session() as db:
        return (
            db.query(func.count(AuditLog.id)).scalar(),
            db.query(func.count(AdminNotification.id)).scalar(),
        )


def _post_bad(client: TestClient, i: int) -> int:
    r = client.post("/api/pay/wechat/notify", json={"id": f"flood-{i}"}, headers=SIG_HEADERS)
    return r.status_code


def test_anomaly_writes_are_bounded_per_minute(client: TestClient):
    """70 次未验签回调：响应全 401（语义不变），但**落库增量被预算截断**。

    RED（还原修复：不判预算）：审计与待办各 +70。
    """
    a0, n0 = _counts()
    codes = [_post_bad(client, i) for i in range(70)]
    a1, n1 = _counts()

    # 响应语义必须不变（微信只认 200；非 2xx 才会重试）。单测走 mock 网关（验签恒放行），
    # 因此实际落在"报文无法解析"分支 → 400；真通道是"验签不通过" → 401。两者都走同一留痕函数。
    assert len(set(codes)) == 1 and 400 <= codes[0] < 500, f"必须是同一个 4xx：{set(codes)}"
    assert a1 - a0 == ANOMALY_PER_MINUTE, f"审计落库应有界，实际 +{a1 - a0}"
    assert n1 - n0 == ANOMALY_PER_MINUTE, f"待办落库应有界，实际 +{n1 - n0}"


def test_single_anomaly_still_recorded(client: TestClient):
    """对照组（**防止修过头**）：单次真实异常照常留痕（审计 + 待办各 1 条）。"""
    a0, n0 = _counts()
    assert 400 <= _post_bad(client, 999) < 500  # 4xx（mock 网关 → 报文分支 400）
    a1, n1 = _counts()
    assert (a1 - a0, n1 - n0) == (1, 1)

    with get_session() as db:
        row = (
            db.query(AuditLog)
            .filter(AuditLog.action == "payment.anomaly")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert row is not None
        # 单测走 mock 网关（验签恒放行）→ 实际落在"解密失败"分支；真通道是"验签不通过"。
        # 三条分支都汇到同一个 `_audit_anomaly`，本用例只要求"确实留痕且是异常类消息"。
        assert json.loads(row.detail)["message"] in (
            "支付回调解密失败",
            "支付回调报文无法解析",
            "支付回调验签不通过",
        )
