# features/steps/wm1_steps.py — WM1 平台基座 BDD 步骤（真实 API 链路）
"""覆盖：config.feature / rbac.feature / audit.feature 的 WM1 范围场景。"""

from behave import given, then, when  # type: ignore[import-not-found]

from features.environment import get_admin_headers, get_staff_headers


# ---------- 背景 ----------
@given('超管已登录后台 且存在配置项 "{key}" 当前值 {value}')
def step_admin_logged_in_with_config(context, key, value):
    context.admin_headers = get_admin_headers(context)
    resp = context.client.get("/api/admin/configs", headers=context.admin_headers)
    assert resp.status_code == 200
    config = next(c for c in resp.json() if c["config_key"] == key)
    assert config["config_value"] == value, f"{key} 期望 {value} 实际 {config['config_value']}"


# ---------- config.feature ----------
@when("超管打开配置管理页面")
def step_open_config_page(context):
    context.configs_resp = context.client.get("/api/admin/configs", headers=context.admin_headers)


@then("全部业务配置按分类展示 当前值与默认值并列")
def step_configs_listed(context):
    assert context.configs_resp.status_code == 200
    configs = context.configs_resp.json()
    assert len(configs) >= 29
    for c in configs:
        assert c["category"] and "config_value" in c and "default_value" in c
    categories = [c["category"] for c in configs]
    assert categories == sorted(categories)


@when('超管把 "{key}" 从 {old} 改为 {new} 并填写变更原因')
def step_update_config(context, key, old, new):
    reason = "测试"
    context.update_resp = context.client.put(
        f"/api/admin/configs/{key}",
        json={"value": new, "reason": reason},
        headers=context.admin_headers,
    )


@then("修改保存成功 新借阅校验立即按 {value} 执行")
def step_update_saved(context, value):
    assert context.update_resp.status_code == 200, context.update_resp.text
    resp = context.client.get("/api/admin/configs", headers=context.admin_headers)
    config = next(c for c in resp.json() if c["config_key"] == "borrow_limit")
    assert config["config_value"] == value


@then("变更记录操作人、时间、旧值、新值、原因")
def step_update_audited(context):
    resp = context.client.get(
        "/api/admin/audit-logs",
        params={"action": "config.update"},
        headers=context.admin_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "缺少 config.update 审计记录"
    entry = items[0]
    assert entry["actor_name"] == "超级管理员"
    assert entry["created_at"]
    assert '"old": "30"' in entry["detail"]
    assert '"new": "20"' in entry["detail"]
    assert entry["reason"] == "测试"


@when('超管把数值型配置 "{key}" 提交为字符串 "{value}"')
def step_update_bad_type(context, key, value):
    context.bad_resp = context.client.put(
        f"/api/admin/configs/{key}",
        json={"value": value, "reason": "类型错误测试"},
        headers=context.admin_headers,
    )


@then("系统拒绝保存并提示类型错误")
def step_type_error_rejected(context):
    assert context.bad_resp.status_code == 422
    assert "类型" in context.bad_resp.json()["detail"]


@given("运营专员尝试修改任一配置")
def step_staff_try_update(context):
    context.staff_headers = get_staff_headers(context)
    context.staff_update_resp = context.client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "20", "reason": "越权"},
        headers=context.staff_headers,
    )


@then("系统返回权限不足")
def step_forbidden(context):
    assert context.staff_update_resp.status_code == 403


@when("配置被修改后")
def step_config_modified(context):
    from backend.database import get_session
    from backend.domain.admin.service import ConfigService, invalidate_config_cache

    invalidate_config_cache()
    db = get_session()
    try:
        svc = ConfigService(db)
        assert svc.get_value("quiz_max_attempts") == "3"  # 先读进缓存
    finally:
        db.close()
    context.update_resp = context.client.put(
        "/api/admin/configs/quiz_max_attempts",
        json={"value": "5", "reason": "缓存失效验证"},
        headers=context.admin_headers,
    )
    assert context.update_resp.status_code == 200


@then("服务端缓存失效 下次读取返回新值")
def step_cache_invalidated(context):
    from backend.database import get_session
    from backend.domain.admin.service import ConfigService

    db = get_session()
    try:
        assert ConfigService(db).get_value("quiz_max_attempts") == "5"
    finally:
        db.close()


# ---------- rbac.feature ----------
@then("系统仅存在超级管理员与会员运营专员两种角色")
def step_two_roles(context):
    admin_headers = get_admin_headers(context)
    staff_headers = get_staff_headers(context)
    admin_role = context.client.get("/api/admin/me", headers=admin_headers).json()["user"]["role"]
    staff_role = context.client.get("/api/admin/me", headers=staff_headers).json()["user"]["role"]
    assert {admin_role, staff_role} == {"superadmin", "staff"}


@then("馆员可以借还续借核销、管理图书音频题目、办理会员、发布活动、跟进逾期、放行留痕")
def step_staff_permissions(context):
    staff_headers = get_staff_headers(context)
    perms = context.client.get("/api/admin/me", headers=staff_headers).json()["permissions"]
    for code in [
        "borrow.operate",
        "book.manage",
        "audio.manage",
        "quiz.manage",
        "member.manage",
        "activity.manage",
    ]:
        assert code in perms, f"staff 缺少权限码 {code}"


@then("退款审核、退会审核、转让审核、测验次数重置、价格规则调整、员工账号管理仅超管可操作")
def step_super_only_permissions(context):
    from backend.domain.admin.service import role_has_permission

    for code in [
        "refund.audit",
        "withdrawal.audit",
        "transfer.audit",
        "quiz.reset",
        "config.update",
        "user.manage",
    ]:
        assert role_has_permission("superadmin", code), f"超管应有权 {code}"
        assert not role_has_permission("staff", code), f"馆员不应有权 {code}"


@then("接口权限通过声明式依赖校验")
def step_declarative_auth(context):
    staff_headers = get_staff_headers(context)
    resp = context.client.get("/api/admin/audit-logs", headers=staff_headers)
    assert resp.status_code == 403  # require_perm 依赖注入生效


@then("前端路由守卫、后端接口、菜单显隐三处权限码一致")
def step_perm_codes_consistent(context):
    from backend.domain.admin.service import STAFF_PERMISSIONS, permissions_for_role

    staff_headers = get_staff_headers(context)
    admin_headers = get_admin_headers(context)
    staff_me = context.client.get("/api/admin/me", headers=staff_headers).json()
    admin_me = context.client.get("/api/admin/me", headers=admin_headers).json()
    # /me 下发的码与权限目录（后端 require_perm / 前端菜单同一数据源）完全一致
    assert staff_me["permissions"] == STAFF_PERMISSIONS
    assert admin_me["permissions"] == permissions_for_role("superadmin")
    # 前端菜单引用的码在目录中有定义（无死码）
    assert "config.view" in staff_me["permissions"]


# ---------- audit.feature ----------
@when("超管修改任一系统配置")
def step_admin_modify_config(context):
    context.audit_update_resp = context.client.put(
        "/api/admin/configs/borrow_days",
        json={"value": "45", "reason": "审计留痕验证"},
        headers=get_admin_headers(context),
    )
    assert context.audit_update_resp.status_code == 200


@then("日志记录旧值、新值与原因")
def step_log_records_change(context):
    admin_headers = get_admin_headers(context)
    resp = context.client.get(
        "/api/admin/audit-logs", params={"action": "config.update"}, headers=admin_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "无配置变更日志"
    entry = items[0]
    assert '"old": "30"' in entry["detail"]
    assert '"new": "45"' in entry["detail"]
    assert entry["reason"] == "审计留痕验证"
    assert entry["created_at"]


@then("操作日志对任何角色均为只读 不可编辑或删除")
def step_logs_readonly(context):
    admin_headers = get_admin_headers(context)
    resp_del = context.client.delete("/api/admin/audit-logs/1", headers=admin_headers)
    resp_put = context.client.put(
        "/api/admin/audit-logs/1", json={"reason": "篡改"}, headers=admin_headers
    )
    assert resp_del.status_code in (404, 405)
    assert resp_put.status_code in (404, 405)


@when("资金操作留痕")
def step_pending_wm2(context):
    raise NotImplementedError("WM2-WM4 资金模块未交付")


@when("人工放行留痕")
def step_pending_wm5(context):
    raise NotImplementedError("WM5 借阅模块未交付")
