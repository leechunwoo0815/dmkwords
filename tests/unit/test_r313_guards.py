# tests/unit/test_r313_guards.py — R-313 小程序权限矩阵专项（docs/10 P0）
"""专家复审 §3 矩阵逐格验证：none/expired/withdrawn × 查词/生词本/收藏/Quiz/
护照/积分/报告/活动列表/押金补缴。withdrawn 经正规退款链路构造（防手工改库掩盖）。"""

from datetime import date, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    return p, c, mini


def _pay(client, h, child_id, order_type):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _pay_deposit(client, h, child_id):
    do = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )


def _mk_activity(client, h, title, member_only):
    r = client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": "2099-01-01T10:00:00",
            "location": "馆内",
            "max_quota": 30,
            "fee": 0,
            "member_only": member_only,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text


def _withdrawn_child(client, h, phone, name="退会孩"):
    """正规链路构造 withdrawn 孩子：会员费退款申请 → 审核通过 → 执行成功 → R-310 联动退会。"""
    p, c, mini = _family(client, h, phone, name)
    order = _pay(client, h, c["id"], "formal_fee")
    _pay_deposit(client, h, c["id"])
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "不学了"},
        headers=mini,
    )
    assert rr.status_code == 200, rr.text
    rid = rr.json()["id"]
    client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "已退"},
        headers=h,
    )
    assert ex.status_code == 200, ex.text
    from backend.database import get_session
    from backend.domain.identity.models import Child

    db = get_session()
    ch = db.query(Child).filter(Child.id == c["id"]).first()
    assert ch.member_status == "withdrawn"
    db.close()
    return c, mini


def test_r313_unpaid_none_matrix(client: TestClient):
    """未缴费（none）：查词/生词本/Quiz/护照/积分/报告禁；收藏可用；活动可见。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001201", "未缴费孩")
    # 查词：禁
    r = client.get(f"/api/miniapp/vocabulary/lookup?word=apple&child_id={c['id']}", headers=mini)
    assert r.status_code == 422
    assert "入会" in r.json()["detail"]
    # 生词本看/删：禁
    assert (
        client.get(f"/api/miniapp/vocabulary?child_id={c['id']}", headers=mini).status_code == 422
    )
    assert (
        client.delete(f"/api/miniapp/vocabulary/1?child_id={c['id']}", headers=mini).status_code
        == 422
    )
    # Quiz：禁
    assert client.get(f"/api/miniapp/quiz/1?child_id={c['id']}", headers=mini).status_code == 422
    # 护照/积分/汇总/报告：禁
    for url in (
        f"/api/miniapp/passport?child_id={c['id']}",
        f"/api/miniapp/points?child_id={c['id']}",
        f"/api/miniapp/growth/summary?child_id={c['id']}",
        f"/api/miniapp/reports/weekly?child_id={c['id']}",
    ):
        rv = client.get(url, headers=mini)
        assert rv.status_code == 422, f"{url} 应被 R-313 拦截"
    # 收藏夹写：可用（R-314）
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9781200000001", "title": "收藏书", "word_count": 100},
        headers=h,
    ).json()
    r = client.post(
        "/api/miniapp/favorites", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert r.status_code == 200, r.text
    # 活动列表：普通可见（R-313 活动列表行"可见"）
    _mk_activity(client, h, "会员专属活动", True)
    acts = client.get(f"/api/miniapp/activities?child_id={c['id']}", headers=mini).json()
    assert any(a["title"] == "会员专属活动" for a in acts)


def test_r313_expired_matrix(client: TestClient, db):
    """过期（formal 到期未落库）：只读类放行；写操作禁；查词仅在借书内。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001202", "过期孩")
    _pay(client, h, c["id"], "formal_fee")
    from backend.domain.identity.models import Child

    ch = db.query(Child).filter(Child.id == c["id"]).first()
    ch.member_expire = date.today() - timedelta(days=1)
    db.commit()
    # 生词本看：允（只读）
    assert (
        client.get(f"/api/miniapp/vocabulary?child_id={c['id']}", headers=mini).status_code == 200
    )
    # 生词本删：禁（只读）
    r = client.delete(f"/api/miniapp/vocabulary/1?child_id={c['id']}", headers=mini)
    assert r.status_code == 422
    assert "只读" in r.json()["detail"]
    # 查词（非在借书）：禁（仅音频场景内）
    r = client.get(f"/api/miniapp/vocabulary/lookup?word=apple&child_id={c['id']}", headers=mini)
    assert r.status_code == 422
    assert "已借" in r.json()["detail"]
    # 护照/积分/报告：允（只读）+ read_only 标记
    r = client.get(f"/api/miniapp/passport?child_id={c['id']}", headers=mini)
    assert r.status_code == 200
    assert r.json()["read_only"] is True
    assert client.get(f"/api/miniapp/points?child_id={c['id']}", headers=mini).status_code == 200
    assert (
        client.get(f"/api/miniapp/reports/weekly?child_id={c['id']}", headers=mini).status_code
        == 200
    )
    # 收藏夹写：允（R-314 过期可收藏）
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9781200000002", "title": "过期收藏书", "word_count": 100},
        headers=h,
    ).json()
    r = client.post(
        "/api/miniapp/favorites", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert r.status_code == 200


def test_r313_withdrawn_matrix(client: TestClient):
    """退会：查词/Quiz/收藏写禁；生词本/护照/积分/报告只读；会员专属活动不可见。"""
    h = _h(client)
    c, mini = _withdrawn_child(client, h, "13800001203", "退会矩阵孩")
    # 查词：禁
    r = client.get(f"/api/miniapp/vocabulary/lookup?word=apple&child_id={c['id']}", headers=mini)
    assert r.status_code == 422
    assert "退会" in r.json()["detail"]
    # Quiz：禁
    r = client.get(f"/api/miniapp/quiz/1?child_id={c['id']}", headers=mini)
    assert r.status_code == 422
    assert "退会" in r.json()["detail"]
    # 收藏夹写：禁（只读）
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9781200000003", "title": "退会收藏书", "word_count": 100},
        headers=h,
    ).json()
    r = client.post(
        "/api/miniapp/favorites", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert r.status_code == 422
    assert "只读" in r.json()["detail"]
    # 生词本/护照/积分/报告：只读放行
    assert (
        client.get(f"/api/miniapp/vocabulary?child_id={c['id']}", headers=mini).status_code == 200
    )
    r = client.get(f"/api/miniapp/passport?child_id={c['id']}", headers=mini)
    assert r.status_code == 200
    assert r.json()["read_only"] is True
    assert client.get(f"/api/miniapp/points?child_id={c['id']}", headers=mini).status_code == 200
    assert (
        client.get(f"/api/miniapp/reports/weekly?child_id={c['id']}", headers=mini).status_code
        == 200
    )
    # 会员专属活动：列表不可见（C20）；公开活动可见
    _mk_activity(client, h, "退会不可见活动", True)
    _mk_activity(client, h, "公开活动", False)
    acts = client.get(f"/api/miniapp/activities?child_id={c['id']}", headers=mini).json()
    titles = [a["title"] for a in acts]
    assert "退会不可见活动" not in titles
    assert "公开活动" in titles
    # 押金补缴：禁（R-313）
    r = client.post(
        "/api/miniapp/deposits/supplement-orders", json={"child_id": c["id"]}, headers=mini
    )
    assert r.status_code == 422
    assert "退会" in r.json()["detail"]


def test_r313_active_member_full_access(client: TestClient):
    """在册：查词/生词本/收藏/护照/积分/报告/补缴守卫全放行（业务层语义另验）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001204", "在册孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    # 查词（生词本命中）
    r = client.get(f"/api/miniapp/vocabulary/lookup?word=apple&child_id={c['id']}", headers=mini)
    assert r.status_code == 200, r.text
    # 生词本看
    assert (
        client.get(f"/api/miniapp/vocabulary?child_id={c['id']}", headers=mini).status_code == 200
    )
    # 护照/积分/报告/汇总
    r = client.get(f"/api/miniapp/passport?child_id={c['id']}", headers=mini)
    assert r.status_code == 200
    assert r.json()["read_only"] is False
    assert client.get(f"/api/miniapp/points?child_id={c['id']}", headers=mini).status_code == 200
    assert (
        client.get(f"/api/miniapp/reports/weekly?child_id={c['id']}", headers=mini).status_code
        == 200
    )
    assert (
        client.get(f"/api/miniapp/growth/summary?child_id={c['id']}", headers=mini).status_code
        == 200
    )
    # 补缴：守卫放行（押金充足 → 业务层"无需补缴"报错 = 守卫已过）
    r = client.post(
        "/api/miniapp/deposits/supplement-orders", json={"child_id": c["id"]}, headers=mini
    )
    assert r.status_code == 422
    assert "无需补缴" in r.json()["detail"]
    # 押金状态查询端点
    dep = client.get(f"/api/miniapp/deposits?child_id={c['id']}", headers=mini).json()
    assert dep["status"] == "paid"
    assert dep["need_supplement"] is False
