# tests/unit/test_audit8_b_batch_20261020.py — 第八轮审查 B 批：安全与可用性 6 条
"""**缺陷（第八轮审查 §3.1 P1-4/5/9/11/12 + §3.2 P2-13/14/15）**：

| # | 缺陷 | 本文件守护 |
|---|---|---|
| P1-4① | 全局写限流在**路由匹配之前**建桶 → 随机路径各建一桶（未认证内存 DoS） | `test_unrouted_write_paths_share_one_bucket` |
| P1-4② | `_identity` 对 `Authorization` **原文** sha1 → 同一 token 换写法 = 换桶 | `test_authorization_spelling_variants_share_bucket` |
| P1-4③ | `RateLimiter` 无锁 → 并发 `RuntimeError`（挂限流的端点偶发 500） | `test_limiter_is_thread_safe` |
| P1-4（附加） | 桶数无硬上限（随机 token / 随机 IP 仍可造桶） | `test_bucket_table_is_hard_capped` |
| P1-5 | 像素闸在 `img.load()`（整图解码）**之后** → 上传可 OOM | `test_pixel_gate_runs_before_decode` |
| P1-9 | 改号查重不含软删 → 撞唯一索引 **500**，且撤销先跑（**失败却已被踢下线**） | `test_phone_change_to_soft_deleted_owner_is_rejected` |
| P1-11 | 调度器单实例锁**无故障转移** → 持锁副本退出后 21 个任务全停 | `test_scheduler_takes_over_after_holder_exits` |
| P1-12 | 任务看板取**全局最新 500 条** → 日级 cron 任务当天下午显示"从未运行" | `test_task_board_window_is_per_task` |
| P2-13 | 生产不拦 `RATE_LIMIT_WRITE_PER_MINUTE<=0` / `SCHEDULER_ENABLED=false` | `test_production_rejects_disabled_rate_limit_and_scheduler` |
| P2-15 | 生产判定三处不一致（`== "production"`）→ `APP_ENV=prod` 时 `/docs` 全开 + 弱口令可播种 | `test_production_env_judgement_is_single_source` |
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import get_session
from backend.middleware import rate_limit as rl
from tests.unit.test_wm11_tasks import _h

# ---------------------------------------------------------------- P1-4 限流


def _write_budget() -> int:
    """全局写限流默认预算（读配置，默认 60）——与中间件 `_write_budget` 同一来源。"""
    from backend.config import get_settings

    return int(getattr(get_settings(), "RATE_LIMIT_WRITE_PER_MINUTE", 60))


def _reset_limiter() -> None:
    """清空限流器桶表：模块级单例，跨用例会互相污染（前一个用例打满的桶会 429 下一个）。"""
    with rl._limiter._lock:
        rl._limiter._requests.clear()


def test_unrouted_write_paths_share_one_bucket(client: TestClient):
    """① 随机路径的写请求必须**共用一个兜底桶**，而不是一个路径一个桶。

    RED（还原修复：只要"是写方法"就建桶）：30 个随机路径 = 30 个桶 → 桶数断言红；
    且第 61 个随机路径写请求不会 429（各桶各算）→ 429 断言红。
    """
    _reset_limiter()
    before = rl._limiter.bucket_count()
    for i in range(30):
        client.post(f"/x-probe-{i}-{'z' * (i % 7)}", json={})
    after = rl._limiter.bucket_count()
    assert after - before <= 2, f"30 个随机路径只该产生 0~1 个桶，实增 {after - before} 个"

    # 共用预算：连发到预算上限后必须 429（证明它们真的挤在同一个桶里）
    codes = [client.post(f"/y-probe-{i}", json={}).status_code for i in range(_write_budget() + 5)]
    assert 429 in codes, f"未路由的写请求必须有统一预算，实得 {sorted(set(codes))}"


def test_authorization_spelling_variants_share_bucket(client: TestClient):
    """② 同一 token 的 5 种写法必须落**同一个桶**（HTTP 语义：scheme 大小写不敏感、空白可折叠）。

    RED（还原修复：对头原文 sha1）：5 种写法 = 5 个桶 → 61 次写请求分散在 5 个桶里，
    一次 429 都不会出现。
    """
    _reset_limiter()
    token = "dummy-token-for-bucket-test"
    variants = [f"Bearer {token}", f"bearer {token}", f"BEARER {token}", f"Bearer  {token}"]
    budget = _write_budget()
    codes = [
        client.post(
            "/api/admin/books",
            json={"isbn": "x"},
            headers={"Authorization": variants[i % len(variants)]},
        ).status_code
        for i in range(budget + 1)
    ]
    assert codes[-1] == 429, (
        f"同一 token 换写法不该换桶：第 {budget + 1} 次应 429，实得 {codes[-1]}"
        f"（前 {budget} 次：{sorted(set(codes[:-1]))}）"
    )


def test_limiter_is_thread_safe():
    """③ 并发下桶表必须**不丢计数、不抛异常**（原先无锁：读改写互相覆盖 + `RuntimeError`）。

    判据为什么用"**计数精确**"而不是"碰巧抛没抛异常"（实测踩坑，如实记）：`RuntimeError:
    dictionary changed size during iteration` 只在"插入恰好触发 dict 扩容、又与遍历重叠"时
    才发生——**概率性**，同一份无锁代码实测会一次红一次绿（假绿）。而"读-改-写覆盖"
    （两个线程各读到同一份 `recent`，后写覆盖前写）在每次交错时**必然**丢一次计数，
    8 线程 × 2000 次同一键 = 统计上必然出现。两者由同一把 `threading.Lock` 保证。
    """
    threads_n, per_thread = 8, 2000
    limiter = rl.RateLimiter()
    errors: list[str] = []
    barrier = threading.Barrier(threads_n)

    def worker() -> None:
        barrier.wait()
        try:
            for _ in range(per_thread):
                limiter.check("shared-key", 10_000_000, 3600)
        except rl.RateLimitError:
            pass  # 限流器**合法**抛这个；本用例只抓并发导致的异常与丢计数
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    ts = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    assert not errors, f"并发访问不得抛异常，实得 {len(errors)} 次：{errors[:3]}"
    with limiter._lock:
        recorded = len(limiter._requests["shared-key"])
    assert recorded == threads_n * per_thread, (
        f"并发计数必须精确（丢计数 = 限流形同虚设）：期望 {threads_n * per_thread}，实得 {recorded}"
    )


def test_bucket_table_is_hard_capped():
    """附加：桶数**硬上限**（随机 token / 随机 IP 也能造桶，有界内存是硬要求）。

    到顶后新键并入溢出桶，**不淘汰活跃桶**（淘汰 = 给攻击者一条"清空我自己的计数"的旁路）。
    """
    limiter = rl.RateLimiter()
    for i in range(rl.RateLimiter._MAX_BUCKETS + 500):
        try:
            limiter.check(f"unique-{i}", 60, 60)
        except rl.RateLimitError:
            pass  # 到顶后新键挤进溢出桶，共享预算被打满 → 合法
    # 不变量：常规桶 ≤ _MAX_BUCKETS，**加溢出桶总计 ≤ _MAX_BUCKETS + 1**
    assert limiter.bucket_count() <= rl.RateLimiter._MAX_BUCKETS + 1, (
        f"桶数必须封顶在 {rl.RateLimiter._MAX_BUCKETS}+1（含溢出桶），实得 {limiter.bucket_count()}"
    )
    # 溢出桶必须真的在收客（而不是丢弃计数 = 等于放行）
    with limiter._lock:
        assert rl.RateLimiter._OVERFLOW_KEY in limiter._requests


# ---------------------------------------------------------------- P1-5 图片解码顺序


def _png_with_huge_header(w: int = 9000, h: int = 9000) -> bytes:
    """构造"header 声明巨幅、但**IDAT 数据仍是 1x1**"的真 PNG（改 IHDR + 重算 CRC）。

    尺寸取 **9000x9000 = 81M px**：① > `MAX_DECODE_PIXELS`(50M) → 必须被像素闸拦；
    ② < Pillow 自带的解压炸弹阈值（50M 的两倍）→ `Image.open()` 只 warn 不抛，
    于是"先判像素还是先解码"**可被判别**：
      · 修复后：像素闸先命中 → `图片像素过大（9000x9000）`
      · 修复前：`img.load()` 先炸（数据截断）→ `图片文件无法解析`
    """
    import io
    import struct
    import zlib

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (10, 20, 30)).save(buf, "PNG")
    raw = bytearray(buf.getvalue())
    assert raw[12:16] == b"IHDR", "PNG 结构变了，先修本函数"
    struct.pack_into(">II", raw, 16, w, h)
    struct.pack_into(">I", raw, 29, zlib.crc32(bytes(raw[12:29])) & 0xFFFFFFFF)
    return bytes(raw)


def test_pixel_gate_runs_before_decode():
    """P1-5：巨幅图必须**在解码之前**被像素闸拒掉（拒绝理由必须是"像素过大"）。

    RED（还原修复：先 `img.load()` 再判像素）：`load()` 先抛 → 理由变成"图片文件无法解析"。
    """
    from backend.common.exceptions import ValidationError
    from backend.common.file_storage import MAX_DECODE_PIXELS, normalize_image

    assert 9000 * 9000 > MAX_DECODE_PIXELS
    with pytest.raises(ValidationError) as exc:
        normalize_image(_png_with_huge_header(), max_edge=800, quality=85)
    assert "像素过大" in str(exc.value), (
        f"巨幅图必须在解码前被像素闸拦下（否则会先解码吃掉上百 MB），实得：{exc.value}"
    )
    assert "9000x9000" in str(exc.value), str(exc.value)


def test_normal_small_image_still_passes():
    """对照组（**防止修过头**）：正常小图照常转码成功。"""
    import io

    from PIL import Image

    from backend.common.file_storage import normalize_image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "PNG")
    out = normalize_image(buf.getvalue(), max_edge=800, quality=85)
    assert out[:2] == b"\xff\xd8", "应输出 JPEG"
    assert Image.open(io.BytesIO(out)).size == (64, 48)


# ---------------------------------------------------------------- P1-9 改号撞软删


def test_phone_change_to_soft_deleted_owner_is_rejected(client: TestClient):
    """P1-9：改号目标属于**已删除档案** → 422 精确文案，且**不得**把该家长踢下线。

    RED（还原修复：查重只看未删行 + 撤销写在查重之前）：
    落库撞唯一索引 → **HTTP 500**；同时 `token_generation` 已被 +1（改号失败但人已被踢）。
    """
    h = _h(client)
    a = client.post(
        "/api/admin/members/parents", json={"name": "甲", "phone": "13800003901"}, headers=h
    ).json()
    b = client.post(
        "/api/admin/members/parents", json={"name": "乙", "phone": "13800003902"}, headers=h
    ).json()
    # 软删乙（该号从此"属于一个已删除的档案"）
    assert client.delete(f"/api/admin/members/parents/{b['id']}", headers=h).status_code == 200

    r = client.patch(
        f"/api/admin/members/parents/{a['id']}", json={"phone": "13800003902"}, headers=h
    )
    assert r.status_code == 422, f"撞软删档案必须 422（不是 500）：{r.status_code} {r.text}"
    assert "已删除的家长档案" in r.json()["detail"], r.json()

    with get_session() as db:
        row = db.execute(
            text("SELECT phone, token_generation FROM parents WHERE id = :i"), {"i": a["id"]}
        ).one()
        assert row[0] == "13800003901", "改号失败不得改动手机号"
        assert int(row[1]) == 0, (
            "改号失败**不得**撤销 token（原先撤销写在查重之前 → 家长被无故踢下线）"
        )


def test_phone_change_to_active_owner_still_rejected_and_revokes_only_on_success(
    client: TestClient,
):
    """对照：撞**在用**账号同样 422；成功改号时 token_generation 才 +1（撤销语义仍在）。"""
    h = _h(client)
    a = client.post(
        "/api/admin/members/parents", json={"name": "甲2", "phone": "13800003903"}, headers=h
    ).json()
    client.post(
        "/api/admin/members/parents", json={"name": "乙2", "phone": "13800003904"}, headers=h
    )
    r = client.patch(
        f"/api/admin/members/parents/{a['id']}", json={"phone": "13800003904"}, headers=h
    )
    assert r.status_code == 422
    assert "已存在家长账号" in r.json()["detail"]

    ok = client.patch(
        f"/api/admin/members/parents/{a['id']}", json={"phone": "13800003905"}, headers=h
    )
    assert ok.status_code == 200, ok.text
    with get_session() as db:
        row = db.execute(
            text("SELECT phone, token_generation FROM parents WHERE id = :i"), {"i": a["id"]}
        ).one()
        assert row[0] == "13800003905"
        assert int(row[1]) == 1, "成功改号必须撤销旧登录（gen 0→1）"


# ---------------------------------------------------------------- P1-11 调度器故障转移


def test_scheduler_takes_over_after_holder_exits(client: TestClient):
    """P1-11：持锁副本退出后，非持锁副本必须**自动接管**（原先永不接管 → 任务全停）。

    做法：用一个外部连接**真持**命名锁模拟"另一个副本在调度" → `start_scheduler()` 返回 False
    且起看护线程 → 释放外部锁 → 看护在下一个重试周期内接管（`_scheduler` 起来）。

    RED（还原修复：拿不到锁直接 return False、无重试）：`_scheduler` 永远是 None。
    """
    from backend.database import engine
    from backend.tasks import registry as reg

    monkeypatch_retry = 1  # 秒：看护周期改短，用例不必等 60 秒
    with engine.connect() as holder:
        got = holder.execute(
            text("SELECT GET_LOCK(:n, 0)"), {"n": reg.SCHEDULER_LOCK_NAME}
        ).scalar()
        # 前置失败的可诊断化（2026-10-21 第一百零九批实测踩到）：本机**已有持锁者**时
        # GET_LOCK 返回 0，最常见原因是"跑 pytest 时 dev 后端还开着"（它的调度器持着这把锁，
        # `/health` 的 `scheduler.has_lock_conn=true` 可见）——宪法 §八 不变纪律要求先
        # `bash scripts/dev.sh stop`。原断言只说"没拿到命名锁"，看不出该去停谁。
        assert got == 1, (
            "用例前置失败：外部连接没拿到命名锁 = 本机已有持锁者。最常见：**dev 后端在跑**"
            "（其调度器持锁；`curl :8002/health` 看 scheduler.has_lock_conn）。"
            "跑 pytest 前必须 `bash scripts/dev.sh stop`（CLAUDE.md §八 不变纪律）。"
        )
        holder.commit()
        try:
            reg.stop_scheduler()
            with _patched(reg, "SCHEDULER_LOCK_RETRY_SECONDS", monkeypatch_retry):
                assert reg.start_scheduler() is False, "锁在别人手里时不该立刻启动调度"
                assert reg._watchdog_thread is not None and reg._watchdog_thread.is_alive(), (
                    "非持锁副本必须启动抢锁看护（原先什么都不做）"
                )
        finally:
            holder.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": reg.SCHEDULER_LOCK_NAME})
            holder.commit()

        deadline = time.time() + 10
        while time.time() < deadline and reg._scheduler is None:
            time.sleep(0.2)
        try:
            assert reg._scheduler is not None, (
                "持锁副本释放锁后，看护必须在本进程接管调度（原先 21 个任务全停）"
            )
            assert reg.scheduler_state()["running"] is True
        finally:
            reg.stop_scheduler()


def test_health_exposes_scheduler_state(client: TestClient):
    """P1-11 可观测面：`/health` 附加 scheduler 状态（不查库、恒 200、字段是加法）。"""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok", "原有字段不受影响（容器探针仍按 status 判活）"
    assert set(body["scheduler"]) == {"enabled", "running", "has_lock_conn", "watchdog"}


# ---------------------------------------------------------------- P1-12 任务看板窗口


def test_task_board_window_is_per_task(client: TestClient):
    """P1-12：日级 cron 任务的最新一条**不得**被高频任务的记录挤出窗口。

    构造：先给 cron 任务写 1 条成功；再给高频任务写 600 条（> 原先的全局 500 窗口）。
    RED（还原修复：全局最新 500 条）：cron 任务那条滑出窗口 → `last_run is None`
    （看板显示"从未运行"，运营据此误判调度器停了）。
    """
    from backend.common.notification_models import TaskRunLog
    from backend.domain.admin.task_specs import build_specs

    cron_task = "media_census"
    busy_task = "overdue_mark"
    with get_session() as db:
        db.add(TaskRunLog(task_name=cron_task, status=TaskRunLog.STATUS_SUCCESS, processed=7))
        db.flush()
        db.bulk_save_objects(
            [
                TaskRunLog(task_name=busy_task, status=TaskRunLog.STATUS_SUCCESS, processed=i)
                for i in range(600)
            ]
        )
        db.commit()

    with get_session() as db:
        specs = {s["name"]: s for s in build_specs(db)}
    assert specs[cron_task]["last_run"] is not None, (
        f"{cron_task} 的最新一条被 {busy_task} 的 600 条挤出窗口——看板会显示'从未运行'"
    )
    assert specs[cron_task]["last_run"]["processed"] == 7
    assert specs[busy_task]["last_run"]["processed"] == 599


def test_task_board_streak_is_per_task(client: TestClient):
    """P1-12 同族：连续失败次数也必须是**按任务**算的（跨任务串台 = 看板误报红）。"""
    from backend.common.notification_models import TaskRunLog
    from backend.domain.admin.task_specs import build_specs

    with get_session() as db:
        # 高频任务：3 连失败
        for _ in range(3):
            db.add(TaskRunLog(task_name="overdue_mark", status=TaskRunLog.STATUS_FAILED))
        # cron 任务：成功（若按全局窗口串台，会把别人的失败算到自己头上）
        db.add(TaskRunLog(task_name="payment_reconcile", status=TaskRunLog.STATUS_SUCCESS))
        db.commit()

    with get_session() as db:
        specs = {s["name"]: s for s in build_specs(db)}
    assert specs["overdue_mark"]["consecutive_failures"] == 3
    assert specs["payment_reconcile"]["consecutive_failures"] == 0


# ---------------------------------------------------------------- P2-13 / P2-15 生产校验


def _prod_settings(**over):
    from backend.config import Settings

    base = dict(
        APP_ENV="production",
        DEBUG=False,
        SECRET_KEY="s" * 40,
        DB_PASSWORD="pw",
        WECHAT_APP_ID="wx",
        WECHAT_APP_SECRET="sk",
        LOGIN_DEV_CODE="",
        CORS_ORIGINS="https://admin.example.com",
        SMS_ENABLED=True,
        SMS_PROVIDER="aliyun",
        SMS_APP_ID="a",
        SMS_APP_KEY="b",
        SMS_SIGN_NAME="c",
        PAYMENT_ENABLED=False,
    )
    base.update(over)
    return Settings(**base)


def test_production_rejects_disabled_rate_limit_and_scheduler():
    """P2-13：这两个开关一句话就能静默废掉一整条防线（`/health` 恒 200 无信号）→ 生产拒启。

    RED（还原修复：不加这两条校验）：两个 `validate_production()` 都静默通过。
    """
    with pytest.raises(RuntimeError) as e1:
        _prod_settings(RATE_LIMIT_WRITE_PER_MINUTE=0).validate_production()
    assert "RATE_LIMIT_WRITE_PER_MINUTE" in str(e1.value)

    with pytest.raises(RuntimeError) as e2:
        _prod_settings(SCHEDULER_ENABLED=False).validate_production()
    assert "SCHEDULER_ENABLED" in str(e2.value)

    # 对照组（防止"加了就永远红"）：默认值（限流 60 / 调度器开）**不得**触发这两条。
    # 注意本环境未装短信 SDK，生产配置本就还有别的红项，故只断言"我这两条没出现"。
    try:
        _prod_settings().validate_production()
    except RuntimeError as exc:
        assert "RATE_LIMIT_WRITE_PER_MINUTE" not in str(exc), exc
        assert "SCHEDULER_ENABLED" not in str(exc), exc


def test_production_env_judgement_is_single_source():
    """P2-15：生产判定唯一来源 = `is_production_env`（不在开发白名单里就算生产，fail-closed）。

    RED（还原修复：`== "production"`）：`prod` / 拼错 / 空串被判成非生产 → `/docs` 全开
    （接口清单公开）、默认弱口令超管可播种。
    """
    from backend.config import is_production_env
    from backend.main import docs_endpoint_urls

    for env in ("production", "prod", "prduction", "PROD", "  prod  ", "", None):
        assert is_production_env(env) is True, f"{env!r} 必须按生产处理（fail-closed）"
        assert docs_endpoint_urls(env or "")["docs_url"] is None, (
            f"APP_ENV={env!r} 时 /docs 必须关闭（否则接口清单公开）"
        )
    for env in ("dev", "development", "local", "test", "DEV"):
        assert is_production_env(env) is False
        assert docs_endpoint_urls(env)["docs_url"] == "/docs", "开发/测试态照常开文档"


def test_seed_refuses_weak_password_when_env_is_typo_production(monkeypatch):
    """P2-15 同族：`APP_ENV=prod`（拼错）时**默认弱口令超管也不许播种**。

    RED（还原修复：`== "production"`）：`prod` 判不出生产 → 弱口令 admin 被播种进"生产"。
    """
    import backend.seeds.seed_admin as sa
    from backend.config import Settings

    monkeypatch.setattr(
        sa, "get_settings", lambda: Settings(APP_ENV="prod", DEBUG=False), raising=False
    )
    # get_settings 是函数内 import 的，故直接打 backend.config 上的符号
    monkeypatch.setattr(
        "backend.config.get_settings", lambda: Settings(APP_ENV="prod", DEBUG=False)
    )
    with pytest.raises(RuntimeError) as e:
        sa.seed()
    assert "禁止播种默认口令" in str(e.value)


# ---------------------------------------------------------------- 小工具


class _patched:
    """临时替换模块属性的最小上下文管理器（不引 pytest-mock 依赖）。"""

    def __init__(self, obj, name: str, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)
        return False
