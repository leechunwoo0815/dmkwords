# tests/unit/test_audit5_p1_batch3_20261018.py — 第五轮大审查·P1 批量（子批 3：资金异常待办键）
"""P1-3：资金异常 / 退款执行失败两类待办用**默认去重键** → 第 2 起静默丢失。

根因（两处同族）：
  · `identity/payment_notify.py::_audit_anomaly`：`ref_id=target_id or "-"` + 不传 `dedup_key`
    （落默认 `"1"`）+ 不传 `ignore_handled` → ① 定位不到对象的异常**全系统挤一个键**；
    ② 一旦被标记已处理，后续同类异常**永久静默**（唯一索引命中已处理行）。
  · `identity/wm10_service.py` 退款执行失败：`ref_id=str(req.id)` + 默认键
    → 同一退款单**换号重试**（attempt 2）再失败时不再提醒。

修法（照 `catalog/damage_ops.py` 赔偿待办先例）：**业务键 + `ignore_handled=True`**——
异常键 `evt:{异常类型}`、无对象时 `ref_id=anomaly:{报文摘要}`；退款执行失败键 `attempt:{gateway_attempts}`。

三条用例逐条**去修复即红**（变异自证见 docs/09 第七十五批）：
  1. 三类不同的资金异常（报文无法解析 / 解密失败 / 找不到订单）→ **各留一条**（修前全系统共 1 条）；
  2. 同一异常被标记已处理后再发生 → **重新提醒**（修前静默丢弃）；
  3. 退款执行失败 → 换号重试（attempt 2）再失败 → **再提醒一条**（修前 1 条）。
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from tests.unit.test_p1_audit_20261015 import _db
from tests.unit.test_wm12_refund_reconcile import (
    _apply_refund,
    _approve,
    _execute,
    _online_paid_order,
    _refund_row,
    _stub_gateway,
)

SCENE_ANOMALY = "admin.payment_anomaly"
SCENE_REFUND_FAILED = "admin.refund_execute_failed"
NOTIFY_URL = "/api/pay/wechat/notify"


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _notify(client: TestClient, body: str):
    """走**真实 HTTP 回调入口**（mock 通道：验签放行，解密=json.loads 密文）。"""
    return client.post(
        NOTIFY_URL,
        content=body,
        headers={
            "content-type": "application/json",
            "Wechatpay-Signature": "sig",
            "Wechatpay-Timestamp": str(int(time.time())),
            "Wechatpay-Nonce": "nonce123456",
        },
    )


def _envelope(ciphertext: str) -> str:
    return json.dumps(
        {
            "id": "evt_x",
            "event_type": "TRANSACTION.SUCCESS",
            "resource_type": "encrypt-resource",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": ciphertext,
                "associated_data": "transaction",
                "nonce": "nonce123456",
            },
        }
    )


BODY_UNPARSABLE = "not-a-json-body"  # ① 报文无法解析
BODY_DECRYPT_FAIL = _envelope("this-is-not-json-either")  # ② 解密失败
BODY_ORDER_MISSING = _envelope(  # ③ 找不到订单
    json.dumps({"out_trade_no": "NO_SUCH_ORDER_2026", "trade_state": "SUCCESS", "amount": 100})
)


def _rows(scene: str) -> list[tuple]:
    """(id, ref_type, ref_id, dedup_key, handled_at) 列表。"""
    from backend.common.admin_notification_models import AdminNotification

    with _db() as db:
        return [
            (r.id, r.ref_type, r.ref_id, r.dedup_key, r.handled_at)
            for r in db.query(AdminNotification)
            .filter(AdminNotification.scene == scene, AdminNotification.is_deleted == 0)
            .all()
        ]


def test_three_distinct_payment_anomalies_each_get_todo(client: TestClient):
    """三类资金异常各留一条待办（修前：`ref_id="-"` + 默认键 → 全系统共 1 条）。

    期望键值**钉死**（两套机制各自可被单独证伪）：`dedup_key=evt:{异常类型}`、
    无对象时 `ref_id=anomaly:{报文 sha1 前 12 位}`。
    """
    import hashlib

    for body in (BODY_UNPARSABLE, BODY_DECRYPT_FAIL, BODY_ORDER_MISSING):
        _notify(client, body)
    rows = _rows(SCENE_ANOMALY)
    assert len(rows) == 3, (
        f"三类不同的资金异常应各留一条待办，实际 {len(rows)} 条："
        f"{[(r[2], r[3]) for r in rows]}——RED=共用 ref_id='-' 与默认去重键"
    )
    by_key = {r[3]: r[2] for r in rows}
    assert set(by_key) == {
        "evt:支付回调报文无法解析",
        "evt:支付回调解密失败",
        "evt:支付回调找不到订单",
    }, f"去重键必须是 `evt:异常类型`：{sorted(by_key)}"
    for body, key in (
        (BODY_UNPARSABLE, "evt:支付回调报文无法解析"),
        (BODY_DECRYPT_FAIL, "evt:支付回调解密失败"),
        (BODY_ORDER_MISSING, "evt:支付回调找不到订单"),
    ):
        expected = "anomaly:" + hashlib.sha1(body.encode()).hexdigest()[:12]
        assert by_key[key] == expected, (
            f"无对象异常应按**报文指纹**各自成键：期望 {expected}，实际 {by_key[key]}"
        )


def test_anomaly_todo_revives_after_handled(client: TestClient):
    """同一异常被标记已处理后再发生 → 必须重新提醒（`ignore_handled=True`）。"""
    from backend.common.admin_notifications import AdminNotifyService

    _notify(client, BODY_UNPARSABLE)
    _notify(client, BODY_UNPARSABLE)  # 同一报文重复投递 → 幂等，仍 1 条
    rows = _rows(SCENE_ANOMALY)
    assert len(rows) == 1, f"同一报文重复投递应幂等，实际 {len(rows)} 条"
    _row_id, ref_type, ref_id, _key, handled = rows[0]
    assert handled is None

    with _db() as db:
        AdminNotifyService(db).mark_handled(ref_type=ref_type, ref_id=ref_id)
        db.commit()
    assert _rows(SCENE_ANOMALY)[0][4] is not None, "前置：应已标记已处理"

    _notify(client, BODY_UNPARSABLE)  # 处理完之后**又**发生同类异常
    rows = _rows(SCENE_ANOMALY)
    assert len(rows) == 1, f"应复活原行而不是新插一行，实际 {len(rows)} 条"
    assert rows[0][4] is None, (
        "已处理的资金异常待办必须被**复活**（handled_at 清空）——"
        "RED=不传 ignore_handled，唯一索引命中已处理行 → 后续同类异常永久静默"
    )


def test_refund_execute_failed_per_attempt_todo(client: TestClient, db, monkeypatch):
    """退款执行失败：换号重试（attempt 2）再失败 → 再提醒一条（修前同单终身一条）。"""
    from backend.common.admin_notifications import AdminNotifyService

    _stub_gateway(monkeypatch, success=False, error="余额不足")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009988")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    req = _refund_row(db, applied["id"])
    assert req.gateway_attempts == 1
    rows = _rows(SCENE_REFUND_FAILED)
    assert len(rows) == 1, f"第 1 次执行失败应留一条，实际 {len(rows)} 条"

    with _db() as db2:
        AdminNotifyService(db2).mark_handled(ref_type=rows[0][1], ref_id=rows[0][2])
        db2.commit()

    _execute(client, h, applied["id"], remark="原路退（重试）")  # attempt 2，同样失败
    req = _refund_row(db, applied["id"])
    assert req.gateway_attempts == 2, "前置：换号重试应递增 attempt"
    rows = _rows(SCENE_REFUND_FAILED)
    assert len(rows) == 2, (
        f"第 2 次执行失败必须**再**提醒一条，实际 {len(rows)} 条："
        f"{[(r[2], r[3]) for r in rows]}——RED=默认去重键，同一退款单终身一条"
    )
    assert rows[0][3] != rows[1][3], f"两次失败的去重键必须不同：{[r[3] for r in rows]}"
