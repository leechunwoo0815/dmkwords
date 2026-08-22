# tests/unit/test_wm1_config.py — 配置中心（真实链路 + 服务层缓存语义）
import pytest
from fastapi.testclient import TestClient

from backend.domain.admin.service import ConfigService, invalidate_config_cache


def _find(client: TestClient, headers: dict, key: str) -> dict:
    resp = client.get("/api/admin/configs", headers=headers)
    assert resp.status_code == 200
    return next(c for c in resp.json() if c["config_key"] == key)


def test_list_configs(client: TestClient, admin_headers: dict) -> None:
    resp = client.get("/api/admin/configs", headers=admin_headers)
    assert resp.status_code == 200
    configs = resp.json()
    assert len(configs) >= 29
    borrow = _find(client, admin_headers, "borrow_limit")
    assert borrow["config_value"] == "30"
    assert borrow["default_value"] == "30"
    assert borrow["category"] == "借阅"
    # 按分类排序
    categories = [c["category"] for c in configs]
    assert categories == sorted(categories)


def test_staff_can_view_configs(client: TestClient, staff_headers: dict) -> None:
    resp = client.get("/api/admin/configs", headers=staff_headers)
    assert resp.status_code == 200


def test_update_config_ok_and_audited(client: TestClient, admin_headers: dict) -> None:
    resp = client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "20", "reason": "测试调整"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["config_value"] == "20"

    # 立即生效（新读走 API）
    assert _find(client, admin_headers, "borrow_limit")["config_value"] == "20"

    # 审计留痕：操作人/旧值/新值/原因
    logs = client.get(
        "/api/admin/audit-logs", params={"action": "config.update"}, headers=admin_headers
    ).json()
    assert logs["total"] >= 1
    entry = logs["items"][0]
    assert entry["actor_name"] == "超级管理员"
    assert '"old": "30"' in entry["detail"]
    assert '"new": "20"' in entry["detail"]
    assert entry["reason"] == "测试调整"


def test_update_config_type_error(client: TestClient, admin_headers: dict) -> None:
    resp = client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "abc", "reason": "x"},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert "整数" in resp.json()["detail"]


def test_update_config_same_value_rejected(client: TestClient, admin_headers: dict) -> None:
    resp = client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "30", "reason": "没变"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_update_config_not_found(client: TestClient, admin_headers: dict) -> None:
    resp = client.put(
        "/api/admin/configs/no_such_key",
        json={"value": "1", "reason": "x"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_update_config_staff_forbidden(client: TestClient, staff_headers: dict) -> None:
    resp = client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "20", "reason": "x"},
        headers=staff_headers,
    )
    assert resp.status_code == 403


def test_bool_coercion(client: TestClient, admin_headers: dict) -> None:
    resp = client.put(
        "/api/admin/configs/allow_unpaid_offline_borrow",
        json={"value": "yes", "reason": "临时开启"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["config_value"] == "true"

    resp_bad = client.put(
        "/api/admin/configs/allow_unpaid_offline_borrow",
        json={"value": "maybe", "reason": "x"},
        headers=admin_headers,
    )
    assert resp_bad.status_code == 422


def test_config_cache_invalidation(db, client: TestClient, admin_headers: dict) -> None:
    invalidate_config_cache()
    svc = ConfigService(db)
    assert svc.get_value("quiz_max_attempts") == "3"  # 首读进缓存

    resp = client.put(
        "/api/admin/configs/quiz_max_attempts",
        json={"value": "5", "reason": "缓存验证"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    # REPEATABLE READ 下旧快照看不到已提交更新：结束当前事务再读
    db.commit()
    # 下一次读取立即返回新值（缓存已失效，不是等 TTL 过期）
    assert svc.get_value("quiz_max_attempts") == "5"


@pytest.mark.parametrize("bad_body", [{"value": ""}, {"reason": ""}, {}])
def test_update_config_request_validation(
    client: TestClient, admin_headers: dict, bad_body: dict
) -> None:
    resp = client.put("/api/admin/configs/borrow_limit", json=bad_body, headers=admin_headers)
    assert resp.status_code == 422
