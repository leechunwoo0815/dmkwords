"""behave 冒烟步骤（走真实 HTTP 层，反假绿纪律）。"""

from behave import given, then, when  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

from backend.main import app


@given("系统服务已启动")
def step_service_up(context) -> None:
    context.client = TestClient(app)


@when("请求健康检查接口")
def step_request_health(context) -> None:
    context.response = context.client.get("/health")


@then("响应状态码为 200")
def step_status_200(context) -> None:
    assert context.response.status_code == 200, context.response.text


@then("响应中 status 为 ok")
def step_status_ok(context) -> None:
    assert context.response.json()["status"] == "ok", context.response.text
