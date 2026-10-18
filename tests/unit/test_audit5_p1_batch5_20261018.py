# tests/unit/test_audit5_p1_batch5_20261018.py — 第五轮大审查·P1 批量（子批 5：微信订阅模板映射）
"""P1-5：订阅模板映射表 7/9 条规则**永不命中** → 退款结果/预约/活动取消三类微信推送整体空转。

根因：`_TITLE_TEMPLATE_RULES` 按**标题子串**匹配，而真实标题是「预约成功」「活动取消」
「退款审核结果」等，与映射表逐条对不上；且「图书损坏通知」走的是**管理待办**通道（不是家长通知）。

修法：映射改按 **scene**（通知的唯一稳定标识）+ 43101（用户未订阅/已拒收）如实记 `skipped`
（原先被当成功写成 `sent`，管理端显示绿色"已送达"）。

四条用例逐条**去修复即红**（变异自证见 docs/09 第七十五批）：
  1. 映射表的每个 scene 必须**真实在用**（机器化断言，专治"永不命中"）；
  2. 推送链路必须把 **scene** 传下去（修前调用方不传 → 映射无从匹配）；
  3. 43101 回执分类为 `skipped` 且回写落 `skipped`（修前写 `sent`）；
  4. 未映射场景/模板未配置 → **如实降级**并写明真实原因。
"""

from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"


def test_scene_template_map_only_contains_live_scenes():
    """映射表的每个 scene 必须在**真实调用点**出现过（防"永不命中"回归）。

    RED（还原修复：改回标题子串映射）：本用例导入的 `_SCENE_TEMPLATE_RULES` 不存在 →
    直接报错；同时旧表 9 条关键字里 7 条在全仓标题里**一次都不出现**（报告实测输出）。
    """
    from backend.common.notifications import (  # noqa: F401  —— 触发场景常量定义
        SCENE_ACTIVITY_CANCEL,
        SCENE_BORROW_DUE_REMIND,
        SCENE_MEMBER_EXPIRE_REMIND,
        SCENE_MONEY_REFUND_RESULT,
        SCENE_RESERVATION_CREATED,
        SCENE_RESERVATION_EXPIRING,
    )
    from backend.integrations.wechat.config import SubscribeTemplate
    from backend.integrations.wechat.subscribe import _SCENE_TEMPLATE_RULES

    # ① 场景常量 → 值（从 notifications.py 取，不手抄）
    src = (BACKEND / "common/notifications.py").read_text(encoding="utf-8")
    consts = dict(re.findall(r'^(SCENE_[A-Z_]+)\s*=\s*"([^"]+)"', src, flags=re.M))
    live: set[str] = set()
    for p in BACKEND.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for name in re.findall(r"scene=(SCENE_[A-Z_]+)", text):
            if name in consts:
                live.add(consts[name])
        for literal in re.findall(r'scene="([a-z_]+\.[a-z_]+)"', text):
            live.add(literal)

    mapped = set(_SCENE_TEMPLATE_RULES)
    missing = mapped - live
    assert not missing, f"映射表里有**永不命中**的场景（全仓无调用点）：{sorted(missing)}"
    assert len(mapped) >= 6, f"映射表被削到只剩 {len(mapped)} 条——通道等于空转"

    # ② 每条映射的模板属性必须真实存在（防手抄错名）
    for scene, attr in _SCENE_TEMPLATE_RULES.items():
        assert hasattr(SubscribeTemplate, attr), f"{scene} → 模板属性 {attr} 不存在"

    # ③ 报告点名的三类高价值场景必须在表内
    for scene in (
        SCENE_MONEY_REFUND_RESULT,
        SCENE_RESERVATION_CREATED,
        SCENE_RESERVATION_EXPIRING,
        SCENE_ACTIVITY_CANCEL,
        SCENE_MEMBER_EXPIRE_REMIND,
        SCENE_BORROW_DUE_REMIND,
    ):
        assert scene in mapped, f"{scene} 必须映射（PRD 要求微信服务通知）"


def test_push_carries_scene_to_subscribe(client: TestClient, monkeypatch):
    """推送链路必须把 scene 传给订阅模块（修前：调用方只传 title → 映射无从匹配）。"""
    from backend.common import notifications as notif
    from backend.common.notification_models import Notification

    settings = notif.get_settings()
    monkeypatch.setattr(settings, "WECHAT_SUBSCRIBE_ENABLED", True, raising=False)
    captured: dict = {}

    def fake_push(openid, title, content, scene="", on_result=None):
        captured.update(openid=openid, title=title, scene=scene)
        return True, "已提交微信（模板 RETURN_REMIND）"

    monkeypatch.setattr("backend.integrations.wechat.subscribe.push_subscribe_message", fake_push)

    _p, c, _mini = _family(client, _h(client), "13800008781")
    db = _db()
    try:
        nid = notif.NotificationService(db).send(
            parent_id=c["parent_id"],
            scene="borrow.due_remind",
            title="借阅到期提醒",
            content="《测试》将于 3 天后到期",
            category=Notification.CATEGORY_BORROW,
            child_id=c["id"],
            openid="openid-stub",
        )
        db.commit()
        assert captured.get("scene") == "borrow.due_remind", (
            f"订阅推送必须带 scene（实际 {captured.get('scene')!r}）——"
            "RED=只传 title，映射表按标题匹配，7/9 条永不命中"
        )
        row = db.query(Notification).filter(Notification.id == nid).first()
        assert row.wechat_status == Notification.WECHAT_QUEUED
    finally:
        db.close()


def test_43101_is_skipped_not_sent(client: TestClient):
    """43101（用户未订阅/已拒收）→ 回执分类为 skipped，回写也落 skipped（修前写 sent）。"""
    from backend.common import notifications as notif
    from backend.common.notification_models import Notification
    from backend.integrations.wechat.subscribe import classify_send_result

    ok, reason, status = classify_send_result({"errcode": 43101, "errmsg": "user refuse"})
    assert (ok, status) == (False, "skipped"), f"43101 必须记 skipped：{(ok, reason, status)}"
    assert "43101" in reason

    assert classify_send_result({"errcode": 0}) == (True, "", None), "正常回执仍应是 sent"

    _p, c, _mini = _family(client, _h(client), "13800008782")
    db = _db()
    try:
        nid = notif.NotificationService(db).send(
            parent_id=c["parent_id"],
            scene="borrow.due_remind",
            title="借阅到期提醒",
            content="内容",
            category=Notification.CATEGORY_BORROW,
            child_id=c["id"],
            openid="openid-stub",
        )
        db.commit()
        notif.NotificationService._writeback(nid, False, "用户未订阅/已拒收（43101）", "skipped")
        db.expire_all()
        row = db.query(Notification).filter(Notification.id == nid).first()
        assert row.wechat_status == Notification.WECHAT_SKIPPED, (
            f"43101 回写必须是 skipped，实际 {row.wechat_status}——"
            "RED=原先当成功写 sent（管理端绿色'已送达'）"
        )
    finally:
        db.close()


def test_unmapped_scene_and_missing_template_degrade_with_true_reason(monkeypatch):
    """未映射场景 / 模板未配置 → 如实降级 + **真实原因**（不伪装成发送失败）。"""
    from backend.common import notifications as notif
    from backend.integrations.wechat.subscribe import push_subscribe_message

    # 前置：通道开关打开（否则先撞"通道未启用"，测不到映射分支）
    monkeypatch.setattr(notif.get_settings(), "WECHAT_SUBSCRIBE_ENABLED", True, raising=False)

    queued, reason = push_subscribe_message(
        "openid-stub", "阅读圈点赞", "有人赞了你", "circle.liked"
    )
    assert queued is False and "场景未映射" in reason and "circle.liked" in reason, reason

    queued, reason = push_subscribe_message(
        "openid-stub", "借阅到期提醒", "内容", "borrow.due_remind"
    )
    assert queued is False and "未配置" in reason and "RETURN_REMIND" in reason, reason
