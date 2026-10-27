# features/steps/activity_detail_steps.py — 活动图文详情 + 往期回顾（FEAT-058/061）
"""B/C 批（2026-09-20 客户需求）解封：图文块（顺序/上限/安全/预览 token/取消锁定）
与往期回顾（24 小时界、参与人数口径、无孩子信息、已取消不入）。

口径来源：PRD §9.4（图文详情）/ §9.5（往期回顾）、`docs/04` C 批验收。
时间态（已开始/已结束/已过期）一律用 DB 直改时间或跑真任务构造，步骤注释写明理由。
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta

from behave import given, then, when

from features.steps.activity_steps import (
    _admin,
    _enrolled_paid,
    _family,
    _mk_activity,
    _past,
    _signin,
)

DEF_IMAGE_DIR = "activity_detail/"


def _png_bytes() -> bytes:
    """最小 PNG（1x1）——上传接口会按图片策略转码，用真图避免走异常分支。"""
    import struct
    import zlib

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _upload_image(ctx, activity_id: int) -> str:
    """上传一张配图，返回相对路径（activity_detail/{aid}_{tag}.jpg）。"""
    r = ctx.client.post(
        f"/api/admin/activities/{activity_id}/detail-images",
        files={"file": ("现场照.png", io.BytesIO(_png_bytes()), "image/png")},
        headers=_admin(ctx),
    )
    assert r.status_code == 200, f"配图上传失败：{r.status_code} {r.text}"
    return r.json()["path"]


def _put_blocks(ctx, activity_id: int, blocks: list[dict]):
    return ctx.client.put(
        f"/api/admin/activities/{activity_id}/detail-blocks",
        json={"blocks": blocks},
        headers=_admin(ctx),
    )


def _set_activity(ctx, activity_id: int, **fields):
    """直接改活动的**时间/状态**（正常流程要等真实流逝或跑任务；造态理由就地写明）。"""
    from datetime import timedelta

    from backend.database import get_session
    from backend.domain.activity.models import Activity

    if "start_at" in fields and "end_at" not in fields:
        # 产品口径（docs/23 §二）：开始时间变了，结束时间跟着走（默认时长 2h），
        # 否则"改成过去"的活动会留下一个未来的 end_at。
        fields["end_at"] = fields["start_at"] + timedelta(hours=2)
    with get_session() as db:
        db.query(Activity).filter(Activity.id == activity_id).update(fields)
        db.commit()


def _detail_blocks_view(ctx, activity_id: int) -> list[dict]:
    r = ctx.client.get(
        f"/api/miniapp/activities/{activity_id}",
        params={"child_id": ctx.fam["children"][0]["id"]},
        headers=ctx.mini,
    )
    assert r.status_code == 200, r.text
    return r.json().get("detail_blocks", [])


# ---------- 图文详情：编辑 / 顺序 / 目录 ----------


@given("管理端有 1 场未开始的活动")
def step_one_upcoming_activity(ctx):
    ctx.activity = _mk_activity(ctx, title="图文读书会", quota=20, fee=50)


@when("运营添加两段文字和一张现场照片 并保存图文详情")
def step_add_blocks(ctx):
    path = _upload_image(ctx, ctx.activity["id"])
    ctx.image_path = path
    ctx.resp = _put_blocks(
        ctx,
        ctx.activity["id"],
        [
            {"type": "paragraph", "text": "第一段：活动介绍"},
            {"type": "image", "path": path, "caption": "现场照"},
            {"type": "paragraph", "text": "第二段：报名须知"},
        ],
    )


@then("图文按「段落/图片」顺序保存到该活动")
def step_blocks_saved(ctx):
    assert ctx.resp.status_code == 200, ctx.resp.text
    blocks = ctx.resp.json()["blocks"]
    assert [b["type"] for b in blocks] == ["paragraph", "image", "paragraph"], blocks
    assert blocks[1]["path"] == ctx.image_path, blocks
    assert blocks[0]["text"] == "第一段：活动介绍", blocks


@then("配图落到独立的 activity_detail 目录（不与书封混放）")
def step_image_dir(ctx):
    assert ctx.image_path.startswith(DEF_IMAGE_DIR), (
        f"配图应在 {DEF_IMAGE_DIR} 下（与书封/活动封面分目录）：{ctx.image_path}"
    )
    from pathlib import Path as _Path

    from backend.common.file_storage import _uploads_root  # noqa: PLC2701 — 同包内部根目录

    assert (_Path(_uploads_root()) / ctx.image_path).is_file(), "配图文件应真实落盘"


@given("某活动的图文顺序是「段落A → 图片B → 段落C」")
def step_blocks_abc(ctx):
    ctx.activity = _mk_activity(ctx, title="顺序调整会", quota=20, fee=50)
    path = _upload_image(ctx, ctx.activity["id"])
    ctx.image_path = path
    r = _put_blocks(
        ctx,
        ctx.activity["id"],
        [
            {"type": "paragraph", "text": "段落A"},
            {"type": "image", "path": path, "caption": "图片B"},
            {"type": "paragraph", "text": "段落C"},
        ],
    )
    assert r.status_code == 200, r.text


@when("运营把段落C 上移一位")
def step_move_block_up(ctx):
    ctx.resp = _put_blocks(
        ctx,
        ctx.activity["id"],
        [
            {"type": "paragraph", "text": "段落A"},
            {"type": "paragraph", "text": "段落C"},
            {"type": "image", "path": ctx.image_path, "caption": "图片B"},
        ],
    )


@then("保存后的顺序是「段落A → 段落C → 图片B」")
def step_order_after_move(ctx):
    assert ctx.resp.status_code == 200, ctx.resp.text
    blocks = ctx.resp.json()["blocks"]
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph", "image"], blocks
    assert [b.get("text") for b in blocks if b["type"] == "paragraph"] == ["段落A", "段落C"], blocks


@then("小程序详情页按调整后的顺序渲染")
def step_view_order_after_move(ctx):
    """接口层真跑：返回给小程序的就是调整后的顺序（渲染顺序即数组顺序）。"""
    ctx.fam = _family(ctx, "顺序家长", ("顺序孩",))
    ctx.mini = ctx.fam["mini"]
    blocks = _detail_blocks_view(ctx, ctx.activity["id"])
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph", "image"], blocks
    assert [b.get("text") for b in blocks if b["type"] == "paragraph"] == ["段落A", "段落C"], blocks


@given("某活动的图文是「段落 → 图片 → 段落」")
def step_blocks_pip(ctx):
    step_blocks_abc(ctx)
    ctx.resp = _put_blocks(
        ctx,
        ctx.activity["id"],
        [
            {"type": "paragraph", "text": "段落A"},
            {"type": "image", "path": ctx.image_path, "caption": "图片B"},
            {"type": "paragraph", "text": "段落C"},
        ],
    )
    assert ctx.resp.status_code == 200, ctx.resp.text


@when("家长打开该活动详情")
def step_parent_open_detail(ctx):
    ctx.fam = _family(ctx, "详情家长", ("详情孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.detail = _detail_blocks_view(ctx, ctx.activity["id"])


@then("页面按同样顺序渲染 且 点图片可全屏预览")
def step_detail_order(ctx):
    assert [b["type"] for b in ctx.detail] == ["paragraph", "image", "paragraph"], ctx.detail
    # 全屏预览是前端交互（miniapp 侧 bindtap→previewImage）；这里断言接口给了可渲染的图片 URL
    img = [b for b in ctx.detail if b["type"] == "image"][0]
    assert "detail-image" in img["image_url"] and "name=" in img["image_url"], img
    assert "v=" in img["image_url"], f"图片 URL 应带版本 token（换图防旧图）：{img}"


@then("图片 URL 带版本 token（换图后不显示旧图）")
def step_image_version_token(ctx):
    """换图后 URL 必须变（否则小程序缓存显示旧图）。"""
    img_before = [b for b in ctx.detail if b["type"] == "image"][0]["image_url"]
    new_path = _upload_image(ctx, ctx.activity["id"])
    r = _put_blocks(
        ctx,
        ctx.activity["id"],
        [
            {"type": "paragraph", "text": "段落A"},
            {"type": "image", "path": new_path, "caption": "换过的图"},
            {"type": "paragraph", "text": "段落C"},
        ],
    )
    assert r.status_code == 200, r.text
    img_after = [b for b in _detail_blocks_view(ctx, ctx.activity["id"]) if b["type"] == "image"][
        0
    ]["image_url"]
    assert img_after != img_before, f"换图后 URL 未变（会显示旧图）：{img_before}"


# ---------- 图文详情：上限 ----------


@given("运营给一场活动添加第 61 个内容块")
def step_61_blocks(ctx):
    ctx.activity = _mk_activity(ctx, title="上限读书会", quota=20, fee=50)
    ctx.blocks_61 = [{"type": "paragraph", "text": f"第 {i} 段"} for i in range(1, 62)]


@then("保存被拒（单场上限 60 块）")
def step_blocks_limit(ctx):
    r = _put_blocks(ctx, ctx.activity["id"], ctx.blocks_61)
    assert r.status_code == 422, f"61 块应拒：{r.status_code} {r.text[:160]}"


@given("运营添加第 31 张配图")
def step_31_images(ctx):
    ctx.activity = _mk_activity(ctx, title="配图上限会", quota=20, fee=50)
    path = _upload_image(ctx, ctx.activity["id"])
    ctx.blocks_31 = [{"type": "image", "path": path, "caption": f"图{i}"} for i in range(1, 32)]
    # 上传接口只存文件不校验张数；张数在保存时按块统计（同一张图重复引用也会被计 31 次）
    assert len(ctx.blocks_31) == 31


@then("保存被拒（单场配图上限 30 张）")
def step_images_limit(ctx):
    r = _put_blocks(ctx, ctx.activity["id"], ctx.blocks_31)
    assert r.status_code == 422, f"31 张配图应拒：{r.status_code} {r.text[:160]}"
    assert "配图最多" in r.text, r.text


@given("单段文字超过 1000 字 或 图注超过 60 字")
def step_over_length(ctx):
    ctx.activity = _mk_activity(ctx, title="字数上限会", quota=20, fee=50)
    ctx.long_text_blocks = [{"type": "paragraph", "text": "字" * 1001}]
    ctx.long_caption_blocks = [
        {"type": "paragraph", "text": "正常段落"},
        {
            "type": "image",
            "path": f"{DEF_IMAGE_DIR}{ctx.activity['id']}_deadbeef.jpg",
            "caption": "注" * 61,
        },
    ]


@then("保存被拒并提示字数上限")
def step_length_limit(ctx):
    r1 = _put_blocks(ctx, ctx.activity["id"], ctx.long_text_blocks)
    assert r1.status_code == 422, f"1001 字应拒：{r1.status_code} {r1.text[:160]}"
    r2 = _put_blocks(ctx, ctx.activity["id"], ctx.long_caption_blocks)
    assert r2.status_code == 422, f"61 字图注应拒：{r2.status_code} {r2.text[:160]}"
    assert "60" in r2.text or "图注" in r2.text, r2.text


# ---------- 图文详情：归属 / 安全 ----------


@given("运营在图文里引用另一个活动的配图")
def step_cross_activity_image(ctx):
    ctx.activity = _mk_activity(ctx, title="归属校验会", quota=20, fee=50)
    other = _mk_activity(ctx, title="别家活动", quota=20, fee=50)
    other_path = _upload_image(ctx, other["id"])
    ctx.resp = _put_blocks(
        ctx, ctx.activity["id"], [{"type": "image", "path": other_path, "caption": "别家的图"}]
    )


@then("保存被拒并提示「不属于本活动」")
def step_cross_activity_rejected(ctx):
    assert ctx.resp.status_code == 422, (
        f"跨活动引用应拒：{ctx.resp.status_code} {ctx.resp.text[:160]}"
    )
    assert "不属于本活动" in ctx.resp.text, ctx.resp.text


@given("引用的文件不存在")
def step_missing_image_file(ctx):
    ctx.activity = _mk_activity(ctx, title="文件不存在会", quota=20, fee=50)
    ghost = f"{DEF_IMAGE_DIR}{ctx.activity['id']}_deadbeef.jpg"
    ctx.resp = _put_blocks(
        ctx, ctx.activity["id"], [{"type": "image", "path": ghost, "caption": "幽灵图"}]
    )


@then("保存被拒并提示重新上传")
def step_missing_image_rejected(ctx):
    assert ctx.resp.status_code == 422, (
        f"文件不存在应拒：{ctx.resp.status_code} {ctx.resp.text[:160]}"
    )
    assert "不存在" in ctx.resp.text and "重新上传" in ctx.resp.text, ctx.resp.text


@given("配图请求里的文件名带路径分隔符或上跳（../ 或子目录）")
def step_traversal_names(ctx):
    ctx.activity = _mk_activity(ctx, title="路径安全会", quota=20, fee=50)
    _admin(ctx)
    ctx.bad_names = [
        "../secret.jpg",
        "sub/dir.jpg",
        f"../{DEF_IMAGE_DIR}{ctx.activity['id']}_x.jpg",
    ]


@then("请求被拒（只接受 basename）")
def step_traversal_rejected(ctx):
    for name in ctx.bad_names:
        r = ctx.client.get(
            f"/api/admin/activities/{ctx.activity['id']}/detail-image",
            params={"name": name, "token": ""},
            headers=_admin(ctx),
        )
        assert r.status_code in (401, 404), f"穿越名应拒：{name} → {r.status_code}"


@given("配图文件名的活动 id 前缀与 URL 里的活动不一致")
def step_prefix_mismatch(ctx):
    ctx.activity = _mk_activity(ctx, title="前缀校验会", quota=20, fee=50)
    other = _mk_activity(ctx, title="前缀别家会", quota=20, fee=50)
    ctx.other_path = _upload_image(ctx, other["id"])
    # 用 A 活动 id 去取 B 活动前缀的文件 → 归属校验必须拒
    ctx.mismatch_name = ctx.other_path.rsplit("/", 1)[-1]


@then("请求被拒（跨活动取图封死）")
def step_prefix_rejected(ctx):
    r = ctx.client.get(
        f"/api/miniapp/activities/{ctx.activity['id']}/detail-image",
        params={"name": ctx.mismatch_name, "child_id": 0},
        headers=_admin(ctx),
    )
    assert r.status_code in (401, 404), f"跨活动取图应拒：{r.status_code} {r.text[:120]}"


# ---------- 管理端预览配图（两类 token 不通用） ----------


@given("管理端编辑器要预览图文配图")
def step_admin_preview(ctx):
    ctx.activity = _mk_activity(ctx, title="预览会", quota=20, fee=50)
    ctx.image_path = _upload_image(ctx, ctx.activity["id"])
    _put_blocks(
        ctx, ctx.activity["id"], [{"type": "image", "path": ctx.image_path, "caption": "预览图"}]
    )
    _admin(ctx)


@then("预览 URL 指向 admin 的 detail-image 端点并带 query token")
def step_admin_preview_url(ctx):
    import re
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    text = (repo / "admin-web/src/api/activities.ts").read_text(encoding="utf-8")
    m = re.search(r"activityDetailImageUrl[\s\S]{0,400}", text)
    assert m, "admin-web 应有 activityDetailImageUrl 构造器"
    body = m.group(0)
    assert "/detail-image" in body and "token=" in body, body[:220]
    # 端点真跑：管理员 token 走 query 参数能取到图
    r = ctx.client.get(
        f"/api/admin/activities/{ctx.activity['id']}/detail-image",
        params={"name": ctx.image_path.rsplit("/", 1)[-1]},
        headers=_admin(ctx),
    )
    assert r.status_code == 200, f"管理端预览应 200：{r.status_code} {r.text[:120]}"


@then("用家长端端点会 401（两类 token 不通用，2026-09-20 实际踩过）")
def step_miniapp_endpoint_rejects_admin(ctx):
    """家长端 `miniapp/activities/{id}/detail-image` 只认家长 token（Bearer），
    管理端 <img> 拿不到家长 token → 必须走 admin 端点（上面那条断言）。"""
    r = ctx.client.get(
        f"/api/miniapp/activities/{ctx.activity['id']}/detail-image",
        params={"name": ctx.image_path.rsplit("/", 1)[-1]},
        headers=_admin(ctx),
    )
    assert r.status_code == 401, f"管理端 token 打家长端点应 401：{r.status_code} {r.text[:120]}"


# ---------- 图文：开始后 / 结束后可写，核心字段不可改；取消后锁定 ----------


@given("某活动已开始 且 报名名额与费用已确定")
def step_activity_started(ctx):
    ctx.activity = _mk_activity(ctx, title="已开始会", quota=20, fee=50, hours=2)
    ctx.fam = _family(ctx, "已开始家长", ("已开始孩",))
    ctx.mini = ctx.fam["mini"]
    _enrolled_paid(ctx, ctx.activity["id"], ctx.fam)
    # 时间回拨到"已开始"：正常流程要等 2 小时，BDD 不等（跑的是同一套守卫判据）
    _set_activity(ctx, ctx.activity["id"], start_at=datetime.now() - timedelta(minutes=30))


@when("运营修改图文详情")
def step_edit_blocks(ctx):
    ctx.resp = _put_blocks(
        ctx, ctx.activity["id"], [{"type": "paragraph", "text": "活动后补充的图文"}]
    )


@then("图文保存成功（纯展示字段）")
def step_edit_blocks_ok(ctx):
    assert ctx.resp.status_code == 200, f"图文应可写：{ctx.resp.status_code} {ctx.resp.text[:160]}"


@when("运营修改名额或费用")
def step_edit_core_fields(ctx):
    ctx.resp = ctx.client.put(
        f"/api/admin/activities/{ctx.activity['id']}",
        json={"max_quota": 30},
        headers=_admin(ctx),
    )


@then("系统拒绝（活动已开始，不可编辑）")
def step_core_rejected_started(ctx):
    assert ctx.resp.status_code == 422, (
        f"已开始应拒改名额：{ctx.resp.status_code} {ctx.resp.text[:160]}"
    )
    assert "不可编辑" in ctx.resp.text or "不" in ctx.resp.text, ctx.resp.text


@given("某活动已结束")
def step_activity_finished(ctx):
    """真任务路径：活动开始超 1 天且无活跃报名 → `activity_auto_finish` 置 finished。"""
    ctx.activity = _mk_activity(ctx, title="已结束会", quota=20, fee=50)
    _set_activity(ctx, ctx.activity["id"], start_at=datetime.now() - timedelta(hours=25))
    r = ctx.client.post("/api/admin/tasks/activity_auto_finish/run", headers=_admin(ctx))
    assert r.status_code == 200, r.text
    from backend.database import get_session
    from backend.domain.activity.models import Activity

    with get_session() as db:
        a = db.query(Activity).filter(Activity.id == ctx.activity["id"]).first()
        assert a.status == Activity.STATUS_FINISHED, f"真任务应置已结束：实 {a.status}"


@then("图文保存成功（活动后补回顾图文）")
def step_edit_blocks_ok_after(ctx):
    assert ctx.resp.status_code == 200, (
        f"结束后图文应可写：{ctx.resp.status_code} {ctx.resp.text[:160]}"
    )


@when("运营修改开始时间、报名截止、名额或费用")
def step_edit_core_fields_after(ctx):
    ctx.resp = ctx.client.put(
        f"/api/admin/activities/{ctx.activity['id']}",
        json={"start_at": (datetime.now() + timedelta(days=7)).isoformat()},
        headers=_admin(ctx),
    )


@then("系统拒绝（守卫只放行纯展示字段）")
def step_core_rejected_finished(ctx):
    assert ctx.resp.status_code == 422, (
        f"结束后应拒改核心字段：{ctx.resp.status_code} {ctx.resp.text[:160]}"
    )


@given("某活动已被门店取消")
def step_activity_cancelled(ctx):
    ctx.activity = _mk_activity(ctx, title="已取消会", quota=20, fee=50)
    r = ctx.client.post(f"/api/admin/activities/{ctx.activity['id']}/cancel", headers=_admin(ctx))
    assert r.status_code == 200, r.text
    # 同步把开始时间推到 26 小时前：往期场景要求它"本来够资格进往期"，取消后才应被排除
    _set_activity(ctx, ctx.activity["id"], start_at=datetime.now() - timedelta(hours=26))


@when("运营尝试修改图文详情")
def step_try_edit_cancelled(ctx):
    ctx.resp = _put_blocks(ctx, ctx.activity["id"], [{"type": "paragraph", "text": "取消后还想改"}])


@then("保存被拒（已取消的活动不再对外展示，不再给改）")
def step_cancelled_blocks_rejected(ctx):
    assert ctx.resp.status_code == 422, (
        f"已取消应拒改图文：{ctx.resp.status_code} {ctx.resp.text[:160]}"
    )
    assert "已取消" in ctx.resp.text, ctx.resp.text


# ---------- 往期回顾 ----------


def _ensure_past_ctx(ctx) -> list[dict]:
    """确保有家长身份并**重新拉一次**往期列表（场景内会先后造两场活动，缓存列表会失准）。"""
    if not getattr(ctx, "fam", None):
        ctx.fam = _family(ctx, "往期观察家长", ("往期观察孩",))
        ctx.mini = ctx.fam["mini"]
    ctx.past_items = _past(ctx)
    return ctx.past_items


def _make_past(ctx, *, title: str, hours_ago: int, checkin: bool, cancel: bool = False) -> dict:
    """造一场"已过期"的活动：先在未开始态报名（报名要求未开始），再把开始时间改成过去。

    参与人数口径：有签到 → 显示签到数；无签到 → 退回报名数（miniapp 列表同款回退）。
    """
    a = _mk_activity(ctx, title=title, quota=20, fee=50 if checkin else 0)
    fam = _family(ctx, f"{title}家长", (f"{title}孩",))
    if checkin:
        from features.steps.activity_steps import _shift_activity_to_now

        paid = _enrolled_paid(ctx, a["id"], fam)
        _shift_activity_to_now(ctx, a["id"])
        assert _signin(ctx, paid["ticket_code"]).status_code == 200
    else:
        r = ctx.client.post(
            f"/api/miniapp/activities/{a['id']}/enroll",
            json={"child_id": fam["children"][0]["id"]},
            headers=fam["mini"],
        )
        assert r.status_code == 200, r.text
        if r.json()["order_id"]:
            _enrolled_paid_order = r.json()["order_id"]
            rr = ctx.client.post(
                f"/api/admin/orders/{_enrolled_paid_order}/confirm-payment",
                json={"pay_method": "scan"},
                headers=_admin(ctx),
            )
            assert rr.status_code == 200, rr.text
    _set_activity(ctx, a["id"], start_at=datetime.now() - timedelta(hours=hours_ago))
    if cancel:
        r = ctx.client.post(f"/api/admin/activities/{a['id']}/cancel", headers=_admin(ctx))
        assert r.status_code == 200, r.text
    return a


@given("某活动开始时间已过去超过 1 天")
def step_past_activity(ctx):
    ctx.activity = _make_past(ctx, title="往期签到会", hours_ago=26, checkin=True)


@when("家长打开活动页的「往期回顾」")
def step_open_past(ctx):
    ctx.fam = _family(ctx, "往期家长", ("往期孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.past_items = _past(ctx)


@then("该活动出现在列表 且 显示参与人数")
def step_past_listed(ctx):
    row = [a for a in ctx.past_items if a["id"] == ctx.activity["id"]]
    assert row, f"往期应含该活动：{[a['title'] for a in ctx.past_items]}"
    assert row[0]["checked_in_total"] == 1, row[0]
    assert row[0]["enrolled_total"] == 1, row[0]


@then("点进详情是只读回顾形态（不显示名额、无报名入口）")
def step_past_readonly(ctx):
    """详情端点仍给活动信息；"往期不显示名额"体现在列表视图不带名额字段（`_activity_view`
    的 `with_quota=False`）——报名入口是前端条件渲染（无名额字段即无入口可渲染）。"""
    row = [a for a in ctx.past_items if a["id"] == ctx.activity["id"]][0]
    assert "max_quota" not in row and "available_quota" not in row, sorted(row)
    assert "enrolled_total" in row and "checked_in_total" in row, sorted(row)


@given("某活动已过期 但仍有人报名未签到（状态停在已发布）")
def step_past_unfinished(ctx):
    ctx.activity = _make_past(ctx, title="过期未结束会", hours_ago=30, checkin=False)


@then("它同样出现在「往期回顾」里")
def step_past_unfinished_listed(ctx):
    items = _ensure_past_ctx(ctx)
    assert any(a["id"] == ctx.activity["id"] for a in items), (
        f"过期但未结束的活动也应进往期：{[a['title'] for a in items]}"
    )


@then("不会因为状态不是「已结束」而消失")
def step_past_keeps_published(ctx):
    from backend.database import get_session
    from backend.domain.activity.models import Activity

    with get_session() as db:
        a = db.query(Activity).filter(Activity.id == ctx.activity["id"]).first()
    assert a.status == Activity.STATUS_PUBLISHED, f"造态应停在已发布：实 {a.status}"
    row = [x for x in ctx.past_items if x["id"] == ctx.activity["id"]][0]
    assert row["checked_in_total"] == 0 and row["enrolled_total"] == 1, row


@then("它不出现在「往期回顾」列表")
def step_past_cancelled_absent(ctx):
    items = _ensure_past_ctx(ctx)
    assert not any(a["id"] == ctx.activity["id"] for a in items), (
        f"已取消的活动不应进往期：{[a['title'] for a in items]}"
    )


@given("某活动的开始时点距现在 {hours:d} 小时")
def step_activity_hours_ago(ctx, hours):
    ctx.activity = _mk_activity(ctx, title=f"距现在{hours}小时会", quota=20, fee=0)
    _set_activity(ctx, ctx.activity["id"], start_at=datetime.now() - timedelta(hours=hours))


@given("某活动的开始时点距现在超过 24 小时")
def step_activity_over_24h(ctx):
    ctx.activity = _mk_activity(ctx, title="超过24小时会", quota=20, fee=0)
    _set_activity(ctx, ctx.activity["id"], start_at=datetime.now() - timedelta(hours=25))


@then("它不出现在「往期回顾」")
def step_not_in_past(ctx):
    items = _ensure_past_ctx(ctx)
    assert not any(a["id"] == ctx.activity["id"] for a in items if "id" in a), (
        "24 小时内的活动不应进往期"
    )


@then("它出现在「往期回顾」")
def step_in_past(ctx):
    items = _ensure_past_ctx(ctx)
    assert any(a["id"] == ctx.activity["id"] for a in items), (
        f"超过 24 小时应进往期：{[a['title'] for a in items]}"
    )


@given("家长打开「往期回顾」列表")
def step_open_past_list(ctx):
    _make_past(ctx, title="字段口径会", hours_ago=48, checkin=True)
    ctx.fam = _family(ctx, "字段家长", ("字段孩",))
    ctx.mini = ctx.fam["mini"]
    ctx.past_items = _past(ctx)


@then("返回只有活动的标题/封面/时间与参与人数聚合")
def step_past_fields(ctx):
    row = ctx.past_items[0]
    for key in ("id", "title", "start_at", "cover_url"):
        assert key in row, f"往期条目缺 {key}：{sorted(row)}"
    assert "enrolled_total" in row and "checked_in_total" in row, sorted(row)


@then("不含任何孩子姓名、头像或可辨识信息")
def step_past_no_child_info(ctx):
    import json as _json

    blob = _json.dumps(ctx.past_items, ensure_ascii=False)
    for forbidden in ("孩子", "child_name", "child_id", "avatar", "parent"):
        assert forbidden not in blob, f"往期列表泄露孩子信息：{forbidden}"


@given("某往期活动有 {n:d} 个已签到报名")
def step_past_with_n_checkins(ctx, n):
    from features.steps.activity_steps import _enrolled_via_admin, _shift_activity_to_now

    ctx.activity = _mk_activity(ctx, title="签到口径会", quota=n + 5, fee=50)
    paids = []
    for i in range(n):
        fam = _family(ctx, f"签到口径家长{i}", (f"签到口径孩{i}",), login=False)
        paids.append(_enrolled_via_admin(ctx, ctx.activity["id"], fam))
    # 报名全部完成后再挪进签到窗（活动开始后管理端建单会被拒）
    _shift_activity_to_now(ctx, ctx.activity["id"])
    for paid in paids:
        assert _signin(ctx, paid["ticket_code"]).status_code == 200
    _set_activity(ctx, ctx.activity["id"], start_at=datetime.now() - timedelta(hours=30))
    ctx.fam = _family(ctx, "口径观察家长", ("口径观察孩",))
    ctx.mini = ctx.fam["mini"]


@then("列表显示的参与人数是 {n:d}")
def step_past_count(ctx, n):
    items = _past(ctx)
    row = [a for a in items if a["id"] == ctx.activity["id"]][0]
    assert row["checked_in_total"] == n, row
    # 小程序列表回退口径：有签到 → 显示签到数
    shown = row["checked_in_total"] or row["enrolled_total"]
    assert shown == n, (row, shown)


@given("该活动没有任何签到记录")
def step_past_no_checkin(ctx):
    ctx.activity = _make_past(ctx, title="无签到口径会", hours_ago=32, checkin=False)
    ctx.fam = _family(ctx, "无签到家长", ("无签到孩",))
    ctx.mini = ctx.fam["mini"]


@then("参与人数退回显示报名人数")
def step_past_fallback_count(ctx):
    items = _past(ctx)
    row = [a for a in items if a["id"] == ctx.activity["id"]][0]
    assert row["checked_in_total"] == 0 and row["enrolled_total"] == 1, row
    shown = row["checked_in_total"] or row["enrolled_total"]
    assert shown == 1, (row, shown)


# ---------- 管理端活动列表筛选 ----------


@given("管理端在活动列表按状态筛选「已结束」")
def step_admin_filter_finished(ctx):
    ctx.finished = _mk_activity(ctx, title="筛选已结束会", quota=20, fee=50)
    ctx.published = _mk_activity(ctx, title="筛选可报名会", quota=20, fee=50)
    _set_activity(ctx, ctx.finished["id"], start_at=datetime.now() - timedelta(hours=25))
    r = ctx.client.post("/api/admin/tasks/activity_auto_finish/run", headers=_admin(ctx))
    assert r.status_code == 200, r.text


@then("只列出已结束（finished）的活动")
def step_admin_list_finished(ctx):
    r = ctx.client.get("/api/admin/activities", params={"status": "finished"}, headers=_admin(ctx))
    assert r.status_code == 200, r.text
    items = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    ids = [a["id"] for a in items]
    assert ctx.finished["id"] in ids, f"已结束活动应在列表：{ids}"
    assert ctx.published["id"] not in ids, f"可报名活动不应混入：{ids}"
    assert all(a["status"] == "finished" for a in items), [(a["id"], a["status"]) for a in items]
