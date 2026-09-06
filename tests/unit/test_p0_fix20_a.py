# tests/unit/test_p0_fix20_a.py — 插修 10 T20a（#9a）：活动发布 aware/naive datetime 撞车
"""红测试：前端 antd DatePicker toISOString() 发送带时区时间（...Z 后缀，aware），
后端 datetime.now() 无时区（naive）——req.start_at <= datetime.now() 直接 TypeError 500。
API 直调（发 naive 字符串）能成功——门禁测试全绿没抓到，用户表单必炸。

修法：schema field_validator 统一剥时区（astimezone 转本地后剥 tzinfo，本地时区语义；
docker-compose TZ=Asia/Shanghai 已固定）。"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _h


def test_activity_create_with_aware_datetime(client: TestClient):
    """修复前：带 Z 的 aware 时间创建活动 → 500 TypeError（RED）。"""
    h = _h(client)
    r = client.post(
        "/api/admin/activities",
        json={
            "title": "时区活动",
            "activity_type": "book_club",
            "start_at": "2026-09-20T15:00:00.000Z",
            "location": "馆内一层",
            "max_quota": 2,
            "fee": "50",
        },
        headers=h,
    )
    assert r.status_code == 200, f"aware 时间应剥时区后创建成功，实 {r.status_code} {r.text[:120]}"

    r2 = client.post(
        "/api/admin/activities",
        json={
            "title": "时区活动2",
            "activity_type": "book_club",
            "start_at": "2026-09-25T15:00:00.000Z",
            "enroll_deadline": "2026-09-24T15:00:00.000Z",
            "max_quota": 2,
            "fee": "0",
        },
        headers=h,
    )
    assert r2.status_code == 200, f"enroll_deadline aware 同款，实 {r2.status_code} {r2.text[:120]}"


def test_activity_create_naive_datetime_regression(client: TestClient):
    """naive 字符串回归不受影响（现有测试/API 直调路径）。"""
    h = _h(client)
    r = client.post(
        "/api/admin/activities",
        json={
            "title": "naive活动",
            "activity_type": "book_club",
            "start_at": "2026-09-26T15:00:00",
            "max_quota": 2,
            "fee": "0",
        },
        headers=h,
    )
    assert r.status_code == 200, f"naive 回归，实 {r.status_code} {r.text[:120]}"
