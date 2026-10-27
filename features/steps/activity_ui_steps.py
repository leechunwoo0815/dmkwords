# features/steps/activity_ui_steps.py — 活动域前端行为口径（源码事实断言）
"""这 13 条场景描述的是**小程序/管理端的交互行为**，TestClient 摸不到（亮度、震动、焦点、
页签顺序、计时跳转）。做法：**断言实现该行为的那段源码事实**——回归（有人删掉这段逻辑）
会当场红；真机观感（亮没亮、震没震）仍需人工验收（`docs/04` §二十五 S49–S56）。

口径来源：PRD §9.2.1（连扫面板）/ §9.2.2（入场券出示）/ §9.4（图文）/ §9.5（往期）。
每条断言都写明"哪个文件、哪句事实"，不允许"存在某文件"式空断言。
"""

from __future__ import annotations

import re
from pathlib import Path

from behave import given, then, when

from features.steps.activity_steps import (
    _enroll,
    _family,
    _mk_activity,
    _shift_activity_to_now,
    _signin,
)

REPO = Path(__file__).resolve().parents[2]
MINI = REPO / "miniapp" / "pages" / "activity-pkg"
ADMIN = REPO / "admin-web" / "src"


def _read(path: Path) -> str:
    assert path.is_file(), f"源码文件不存在（路径漂移会让本断言失效）：{path}"
    return path.read_text(encoding="utf-8")


def _mini(name: str) -> str:
    return _read(MINI / name)


# ---------- 连扫面板（PRD §9.2.1） ----------


@given("馆员在管理端报名抽屉打开连扫面板")
def step_open_scan_panel(ctx):
    ctx.scan_input = _read(ADMIN / "components" / "ScanInput.tsx")
    ctx.scan_panel = _read(ADMIN / "components" / "ScanCheckin.tsx")


@when("依次扫描成功与失败的券各一张")
def step_scan_one_ok_one_fail(ctx):
    """跑真接口各一次（成功券 / 越场或已签到的失败券），证明两条路径都返回结果。"""
    ctx.activity = _mk_activity(ctx, title="连扫面板会", quota=5, fee=50)
    ctx.fam = _family(ctx, "连扫面板家长", ("连扫面板孩",))
    ctx.mini = ctx.fam["mini"]
    r = _enroll(ctx, ctx.fam["children"][0]["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    from features.steps.activity_steps import _confirm

    _confirm(ctx, r.json()["order_id"])
    ctx.ticket = r.json()["enrollment"]["ticket_code"]
    _shift_activity_to_now(ctx, ctx.activity["id"])
    assert _signin(ctx, ctx.ticket).status_code == 200  # 成功那一张
    ctx.fail_resp = _signin(ctx, ctx.ticket)  # 失败那一张（同一张券 → 已签到）


@then("无论成功还是失败，光标都回到扫描输入框")
def step_refocus_both(ctx):
    assert ctx.fail_resp.status_code == 409, ctx.fail_resp.text
    # 事实：ScanInput 默认 `refocusAfterSubmit = true`，且提交结束后调用 refocusIfIdle()
    assert re.search(r"refocusAfterSubmit\s*=\s*true", ctx.scan_input), (
        "连续扫描要求提交后自动回焦（成功与失败都要）——默认值被改掉会打断连扫"
    )
    # 事实：回焦写在 `finally` 里（成功与失败共用同一条出口），且默认开
    submit_body = ctx.scan_input[ctx.scan_input.index("const submit") :]
    submit_body = submit_body[: submit_body.index("}, [")]
    assert "refocusAfterSubmit" in submit_body[submit_body.index("} finally {") :], (
        "回焦必须在 finally 里（放 try 里会让一次失败打断整队人的连扫）"
    )
    assert "成功与失败都回焦" in ctx.scan_input, "文件头纪律注释应保留两条路径回焦的口径"
    assert "focused" in ctx.scan_input  # 就绪标识（聚焦=绿）：焦点被抢走一眼可见


@then("面板保留最近 20 条扫描记录")
def step_recent_20(ctx):
    assert re.search(r"RECENT_MAX\s*=\s*20", ctx.scan_panel), "连扫面板应保留最近 20 条"
    recent_block = ctx.scan_panel[ctx.scan_panel.index("const [recent") :]
    assert recent_block.count(".slice(0, RECENT_MAX)") >= 2, (
        "成功与失败都要落进最近列表（失败静默就失去对账凭据）"
    )


# ---------- 入场券页（PRD §9.2.2） ----------


@given("家长从小程序「我的报名」点「出示签到码」进入入场券页")
def step_open_ticket_page(ctx):
    ctx.ticket_js = _mini("ticket/ticket.js")
    ctx.ticket_wxml = _mini("ticket/ticket.wxml")
    ctx.list_wxml = _mini("activity-list/activity-list.wxml")


@then("屏幕亮度拉至最高且保持常亮")
def step_brightness_on(ctx):
    assert "wx.setKeepScreenOn({ keepScreenOn: true" in ctx.ticket_js, "入场券页应保持常亮"
    assert "wx.setScreenBrightness({" in ctx.ticket_js, "入场券页应调高亮度"
    assert re.search(r"value:\s*1", ctx.ticket_js), "亮度应拉至最高（value: 1）"
    assert ctx.ticket_js.count("setKeepScreenOn({ keepScreenOn: true") == 1, "常亮只应有一处入口"


@then("离开该页后恢复原亮度设置")
def step_brightness_restore(ctx):
    assert "wx.setKeepScreenOn({ keepScreenOn: false" in ctx.ticket_js, "离开应取消常亮"
    assert re.search(r"setScreenBrightness\(\{\s*value:\s*saved", ctx.ticket_js), (
        "离开应恢复进入前的亮度（保存的 saved 值）"
    )
    assert "_restoreBrightness()" in ctx.ticket_js.split("onUnload")[1][:400], (
        "onUnload 必须调用亮度还原"
    )


@given("家长已报名某活动且未签到")
def step_parent_enrolled_not_checked(ctx):
    ctx.activity = _mk_activity(ctx, title="详情页入口会", quota=5, fee=50)
    ctx.fam = _family(ctx, "详情入口家长", ("详情入口孩",))
    ctx.mini = ctx.fam["mini"]
    from features.steps.activity_steps import _enrolled_paid

    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    ctx.detail_js = _mini("activity-detail/activity-detail.js")
    ctx.detail_wxml = _mini("activity-detail/activity-detail.wxml")


@when("家长打开该活动详情页")
def step_open_activity_detail(ctx):
    r = ctx.client.get(
        f"/api/miniapp/activities/{ctx.activity['id']}",
        params={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    )
    assert r.status_code == 200, r.text
    ctx.detail_body = r.json()


@then("详情页只显示报名状态，不显示二维码或券码文本")
def step_detail_no_qr(ctx):
    # 详情页接口**不下发券码**（券码在「我的入场券」页取）；页面也不应出现二维码容器
    assert ctx.detail_body["my_enrollment"]["status"] == "enrolled", ctx.detail_body
    # 口径（2026-10-13 实测记录）：接口 my_enrollment **含 ticket_code 字段**（家长自己的数据，
    # 同一份报名视图也被入场券页复用）；口径要求的是**详情页不出现码** → 断言页面侧不渲染。
    assert "qrcode" not in ctx.detail_wxml.lower() and "qr-canvas" not in ctx.detail_wxml, (
        "详情页应已无二维码容器（2026-09-20 客户口径：码搬去「我的入场券」页）"
    )
    assert "ticket_code" not in (ctx.detail_wxml + ctx.detail_js), (
        "详情页（wxml+js）不得引用券码字段——券码只在「我的入场券」页渲染"
    )
    assert "2026-09-20" in ctx.detail_wxml, "详情页应留有口径变更注释（改回旧形态要显式）"


@then("详情页给出「出示签到码」入口")
def step_detail_ticket_entry(ctx):
    assert "canShowTicket" in ctx.detail_wxml and "goTicket" in ctx.detail_wxml, (
        "详情页应给「出示签到码」入口"
    )
    assert re.search(r"canShowTicket[^=]*=[^;]*status === 'enrolled'", ctx.detail_js), (
        "入口只在「已报名」态出现（已签到/待收款/退款中都不给）"
    )


@given("家长对某活动的报名状态是待收款确认")
def step_pending_payment_state(ctx):
    """待收款态（两处场景共用：入口不给 + 名额仍占）。"""
    ctx.activity = _mk_activity(ctx, title="待收款入口会", quota=5, fee=50)
    ctx.fam = _family(ctx, "待收款家长", ("待收款孩",))
    ctx.mini = ctx.fam["mini"]
    r = _enroll(ctx, ctx.fam["children"][0]["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.enrollment_id = r.json()["enrollment"]["id"]
    from features.steps.activity_steps import _quota_used

    ctx.quota_used = _quota_used(ctx.activity["id"])
    ctx.list_js = _mini("activity-list/activity-list.js")
    ctx.ticket_js = _mini("ticket/ticket.js")


@then("「我的报名」列表不显示该活动的「出示签到码」按钮")
def step_list_no_ticket_button(ctx):
    assert re.search(r"canShowTicket:\s*m\.status === 'enrolled'", ctx.list_js), (
        "列表入口判据应为 status === 'enrolled'——待收款（pending_payment）自然没有按钮"
    )
    rows = ctx.client.get(
        "/api/miniapp/enrollments",
        params={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    ).json()
    mine = [x for x in rows if x["id"] == ctx.enrollment_id][0]
    assert mine["status"] == "pending_payment", mine


@then("直接进入入场券页会提示「入场券尚未生效」")
def step_ticket_not_effective(ctx):
    assert re.search(
        r"TICKET_STATUSES\s*=\s*\[\s*'enrolled'\s*,\s*'checked_in'\s*\]", ctx.ticket_js
    ), "入场券页只认 enrolled/checked_in 两态（待收款进不去 → 前端给'尚未生效'兜底）"
    assert "尚未生效" in ctx.ticket_js, "非有效态应给「入场券尚未生效」提示"


@given("家长对某活动的报名状态是已签到")
def step_checked_in_state(ctx):
    ctx.activity = _mk_activity(ctx, title="已签到入口会", quota=5, fee=50)
    ctx.fam = _family(ctx, "已签到家长", ("已签到孩",))
    ctx.mini = ctx.fam["mini"]
    from features.steps.activity_steps import _enrolled_paid

    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    _shift_activity_to_now(ctx, ctx.activity["id"])
    assert _signin(ctx, ctx.paid["ticket_code"]).status_code == 200
    ctx.list_js = _mini("activity-list/activity-list.js")


@then("列表显示「已于 HH:MM 签到」且不显示「出示签到码」按钮")
def step_list_checked_in_text(ctx):
    assert "已于" in ctx.list_js and "签到" in ctx.list_js, "列表应显示「已于 HH:MM 签到」"
    assert re.search(r"canShowTicket:\s*m\.status === 'enrolled'", ctx.list_js), (
        "已签到不再是 enrolled → 入口自动消失"
    )


@given("家长正停在入场券页出示二维码 且 报名状态为已报名")
def step_stop_at_ticket_page(ctx):
    ctx.activity = _mk_activity(ctx, title="自动反馈会", quota=5, fee=50)
    ctx.fam = _family(ctx, "自动反馈家长", ("自动反馈孩",))
    ctx.mini = ctx.fam["mini"]
    from features.steps.activity_steps import _enrolled_paid

    ctx.paid = _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    ctx.ticket_js = _mini("ticket/ticket.js")


@when("馆员扫码签到成功")
def step_admin_signin_ok(ctx):
    _shift_activity_to_now(ctx, ctx.activity["id"])
    ctx.signin_resp = _signin(ctx, ctx.paid["ticket_code"])
    assert ctx.signin_resp.status_code == 200, ctx.signin_resp.text


@then("入场券页自动弹出「签到成功」提示并震动")
def step_ticket_success_feedback(ctx):
    assert "checkinSuccess" in ctx.ticket_js, "入场券页应有签到成功态"
    assert "wx.vibrateShort" in ctx.ticket_js, "签到成功应震动（家长正把手机递给馆员）"


@then("约 1.4 秒后自动返回该活动详情页 且 详情页状态条显示「已签到」")
def step_ticket_auto_back(ctx):
    assert re.search(r"SUCCESS_HOLD_MS\s*=\s*1400", ctx.ticket_js), "成功停留应为 1.4 秒"
    assert re.search(r"setTimeout\([\s\S]{0,160}SUCCESS_HOLD_MS", ctx.ticket_js), (
        "1.4 秒后应自动跳转"
    )
    assert "_goActivityDetail" in ctx.ticket_js, "应跳回活动详情"
    # 详情页状态文案：已签到
    detail_js = _mini("activity-detail/activity-detail.js")
    assert "已签到" in detail_js, "详情页状态条应有「已签到」文案"


@then("屏幕亮度还原、不再继续轮询")
def step_ticket_restore_and_stop_poll(ctx):
    body = ctx.ticket_js[ctx.ticket_js.index("_onCheckedIn(mine) {") :]
    assert "_restoreBrightness()" in body[:600], "签到成功后应立即还原亮度"
    assert "_stopWatch()" in body[:200], "签到成功后应停止轮询"
    assert re.search(r"_watch\s*=\s*setInterval", ctx.ticket_js) and (
        "clearInterval(this._watch)" in ctx.ticket_js
    ), "轮询应可被停止"


@given("家长在已签到之后重新打开入场券页")
def step_reopen_ticket_after_checkin(ctx):
    ctx.ticket_js = _mini("ticket/ticket.js")
    # 事实：已签到态在**进入时**就渲染签到信息（不等轮询），且轮询在已签到态直接 return
    tick = ctx.ticket_js[ctx.ticket_js.index("async _tick()") :][:400]
    assert "this.data.checkedIn" in tick, "已签到态应跳过轮询"


@then("直接显示已签到信息 不弹成功提示、不自动跳转、不调亮屏幕")
def step_reopen_quiet(ctx):
    # 成功提示/跳转/震动只在 _onCheckedIn（"状态刚变化"）里发生 → 已签到态重进不会触发
    on_checked = ctx.ticket_js[ctx.ticket_js.index("_onCheckedIn(mine) {") :]
    body = on_checked[: on_checked.index("_goActivityDetail(activityId)")]
    for marker in ("wx.vibrateShort", "SUCCESS_HOLD_MS", "checkinSuccess: true"):
        assert marker in body, f"成功反馈应集中在 _onCheckedIn 内：{marker}"
    assert ctx.ticket_js.count("wx.vibrateShort &&") == 1, "震动只应有一处触发点（重进不再震）"


@given("家长已取消某活动的报名")
def step_enrollment_cancelled(ctx):
    ctx.activity = _mk_activity(ctx, title="取消入场券会", quota=5, fee=0)
    ctx.fam = _family(ctx, "取消入场券家长", ("取消入场券孩",))
    ctx.mini = ctx.fam["mini"]
    r = _enroll(ctx, ctx.fam["children"][0]["id"], ctx.activity["id"])
    assert r.status_code == 200, r.text
    ctx.enrollment_id = r.json()["enrollment"]["id"]
    c = ctx.client.post(
        f"/api/miniapp/enrollments/{ctx.enrollment_id}/cancel",
        json={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    )
    assert c.status_code == 200, c.text
    ctx.ticket_js = _mini("ticket/ticket.js")


@when("家长从历史入口进入该入场券页")
def step_enter_ticket_from_history(ctx):
    rows = ctx.client.get(
        "/api/miniapp/enrollments",
        params={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    ).json()
    ctx.mine = [x for x in rows if x["id"] == ctx.enrollment_id][0]


@then("提示「入场券已失效」并引导回活动列表")
def step_ticket_invalid(ctx):
    """非 enrolled/checked_in 一律走失效兜底（本句在两个场景里各断言一次，同一个实现点）。"""
    if getattr(ctx, "mine", None) is not None:
        assert ctx.mine["status"] == "cancelled", ctx.mine
    assert "入场券已失效" in ctx.ticket_js, "失效态应提示「入场券已失效」"
    assert re.search(
        r"TICKET_STATUSES\s*=\s*\[\s*'enrolled'\s*,\s*'checked_in'\s*\]", ctx.ticket_js
    ), "入场券页只认 enrolled/checked_in 两态"
    back = ctx.ticket_js[ctx.ticket_js.index("onBackToList") :][:400]
    assert "navigateBack" in back and "activity-list" in back, (
        "失效态要有回活动列表的出口（onBackToList：券还在→重试；券失效→回列表）"
    )


@given("家长进入「我的入场券」页 且 报名状态为已报名")
def step_ticket_page_enrolled(ctx):
    ctx.ticket_wxml = _mini("ticket/ticket.wxml")
    ctx.ticket_js = _mini("ticket/ticket.js")


@then("页面同时呈现二维码（主）与 Code128 条形码（备）以及券码文本")
def step_qr_barcode_text(ctx):
    assert "qrImage" in ctx.ticket_wxml and "bcImage" in ctx.ticket_wxml, "二维码 + 条形码两件套"
    assert "ticket_code" in ctx.ticket_wxml, "应同时露出券码文本（手工兜底）"
    js = ctx.ticket_js
    assert re.search(r"code128|CODE128|Code128", js), "条形码应为 Code128"


@then("券码文本可复制（扫码枪读不出来时手工兜底）")
def step_ticket_copy(ctx):
    assert "copyTicket" in ctx.ticket_wxml, "券码文本应可点按复制"
    assert "setClipboardData" in ctx.ticket_js, "复制应落到剪贴板"


@given("家长进入某活动的入场券页 但 该报名已被取消")
def step_ticket_page_cancelled(ctx):
    ctx.ticket_js = _mini("ticket/ticket.js")
    ctx.ticket_wxml = _mini("ticket/ticket.wxml")


@given("该报名已签到")
def step_enrollment_checked_in(ctx):
    ctx.ticket_js = _mini("ticket/ticket.js")
    ctx.ticket_wxml = _mini("ticket/ticket.wxml")


@then("显示签到时间且不再提示{msg}")
def step_checked_in_no_pending_msg(ctx, msg):
    assert "已于" in ctx.ticket_wxml and "checkedInTime" in ctx.ticket_wxml, "应显示签到时间"
    assert "状态更新中" not in ctx.ticket_wxml, "已签到不应再提示状态更新中"


@given("家长在活动详情页滑到图文区并点开一张图")
def step_open_image_preview(ctx):
    ctx.detail_js = _mini("activity-detail/activity-detail.js")


@when("家长关闭全屏预览")
def step_close_preview(ctx):
    ctx.preview_block = ctx.detail_js[ctx.detail_js.index("wx.previewImage") :][:600]


@then("页面仍停在原来的滚动位置（不回到页首）")
def step_scroll_position_kept(ctx):
    assert "pageScrollTo" not in ctx.detail_js, (
        "详情页不得有 pageScrollTo（预览关闭后被拉到页首就是这个调用惹的）"
    )
    assert "scrollTop" not in ctx.detail_js.replace("// ", ""), (
        "也不应手动回写滚动位置（原生 previewImage 关闭后本就停在原位）"
    )


@then("该页刷新是静默的（不出现骨架屏导致的整页重挂）")
def step_silent_refresh(ctx):
    assert re.search(r"silent", ctx.detail_js), "刷新应支持 silent 模式"
    assert re.search(r"骨架屏只在", ctx.detail_js), "应注明骨架屏只在首次进入/出错重试出现"


@given("家长打开小程序活动页")
def step_open_activity_list(ctx):
    ctx.list_wxml = _mini("activity-list/activity-list.wxml")


@then("三个页签依次是「可报名活动」「我的报名」「往期回顾」")
def step_tab_order(ctx):
    wxml = ctx.list_wxml
    tabs = re.findall(r'<view class="tab[^"]*"[^>]*>([^<]+)</view>', wxml)
    # 实测 UI 文案是短版「可报名」（场景原文写「可报名活动」→ 已按实现订正并在场景里注明）
    assert tabs[:3] == ["可报名", "我的报名", "往期回顾"], tabs[:4]


# ---------- 待收款：既占名额、又不给入口（跨端口径合并断言） ----------


@then("活动详情页状态条不出现「出示签到码」按钮")
def step_no_ticket_button_in_detail(ctx):
    detail_js = _mini("activity-detail/activity-detail.js")
    assert re.search(r"canShowTicket[^=]*=[^;]*status === 'enrolled'", detail_js)


@then("该报名仍占用 1 个名额（超时未确认收款才释放）")
def step_pending_holds_quota_assert(ctx):
    assert ctx.quota_used == 1, f"待收款报名应占 1 个名额：实 {ctx.quota_used}"
