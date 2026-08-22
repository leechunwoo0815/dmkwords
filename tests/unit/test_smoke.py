"""冒烟测试：应用可导入 + 健康检查真实响应（真实链路纪律的最小体现）。"""

from fastapi.testclient import TestClient

from backend.main import app


def test_app_importable_and_health() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "DmkWords API"
