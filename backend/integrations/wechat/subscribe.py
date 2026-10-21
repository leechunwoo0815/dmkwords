# backend/integrations/wechat/subscribe.py
"""微信订阅消息集成 — access_token 委托 WeChatService 统一管理"""

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx

from backend.common.exceptions import PaymentError, ValidationError
from backend.integrations.wechat.config import SubscribeTemplate

logger = logging.getLogger(__name__)

#: 推送工作线程数（P2-42，2026-10-20 第八轮大审查）。
#: 原先**每条通知起一个 daemon 线程**（`threading.Thread(...).start()`，无上限）：6000 条提醒
#: = 6000 个线程，每个线程回写时还要各占一条数据库连接（池 10+20）→ 连接池直接打爆。
#: 8 的取法：每个 worker 在回写时会占 1 条连接，留足请求链余量（池 10+20）。
PUSH_WORKERS = 8
#: 积压上限（待发队列硬上限）。满了**如实记 failed + 真实原因**，既不静默丢弃也不无限排队。
PUSH_QUEUE_MAX = 512
_PUSH_POOL = ThreadPoolExecutor(max_workers=PUSH_WORKERS, thread_name_prefix="wechat-push")
_PUSH_BACKLOG = threading.BoundedSemaphore(PUSH_QUEUE_MAX)

# 高价值**通知场景** → 模板映射（P1-5，2026-10-18 第七十五批）。
#
# 为什么按 scene 而不是标题子串：原实现按标题匹配，而真实标题是「预约成功」「活动取消」
# 「退款审核结果」等，与映射表逐条对不上 —— 9 条规则里 **7 条永不命中**，退款结果/预约/活动取消
# 三类高价值消息的微信通道**整体空转**（只落 `skipped`，不是事故，但通道等于没接）。
# scene 是通知的唯一稳定标识（`NotificationService.send(scene=...)`），标题会改、scene 不会。
#
# 只推"需及时触达"的消息（订阅消息是一次授权一次推送的稀缺通道）；未映射的场景
# **如实降级**为"仅站内消息"并写明原因，不伪装成发送失败。
_SCENE_TEMPLATE_RULES: dict[str, str] = {
    "member.expire_remind": "MEMBER_EXPIRE_REMIND",
    "borrow.due_remind": "RETURN_REMIND",
    "reservation.created": "RESERVATION_READY",  # 「预约成功，请到馆取书」
    "reservation.expiring": "RESERVATION_EXPIRING",
    "activity.cancel": "ACTIVITY_REMIND",  # 微信后台无独立"取消"模板，复用活动提醒位
    "money.refund_result": "REFUND_RESULT",  # 审核通过/拒绝同场景（文案区分）
}
# 刻意不映射（原表里的死规则，如实记录）：
#   · 「图书损坏通知」走的是**管理待办**通道（`AdminNotification`），不进家长订阅消息；
#   · 「您等候的图书到货啦」与「预约成功」是同一个场景（`reservation.created`），不重复映射；
#   · 其余场景（借还成功/逾期/成长/报表/阅读圈等）不在"及时触达"清单内。


def get_template_for_scene(scene: str) -> str:
    """按**通知场景**取模板 ID（未配置/未映射返回空串 → 调用方如实降级）。"""
    attr = _SCENE_TEMPLATE_RULES.get(scene or "", "")
    return getattr(SubscribeTemplate, attr, "") or "" if attr else ""


def classify_send_result(result: object) -> tuple[bool, str, str | None]:
    """微信回执分类（P1-5）：返回 `(ok, reason, status)`。

    **43101 = 用户未订阅/已拒收**：既不是"已送达"，也不是"发送故障"——如实记 `skipped`
    （原实现把它当成功返回 → 库里的 `wechat_status` 写成了 sent，管理端显示绿色"已送达"）。
    其它情况（`errcode=0`）为 sent；40003/41028-30 与其它错误码由 `WeChatSubscribe.send`
    抛异常，调用方 except 分支记 failed。
    """
    if isinstance(result, dict) and int(result.get("errcode") or 0) == 43101:
        return False, "用户未订阅/已拒收（43101）", "skipped"
    return True, "", None


def _scene_template(scene: str) -> tuple[str, str]:
    """`(模板属性名, 模板 ID)`——场景未映射时属性名/ID 均为空串。"""
    attr = _SCENE_TEMPLATE_RULES.get(scene or "", "")
    if not attr:
        return "", ""
    return attr, (getattr(SubscribeTemplate, attr, "") or "")


def precheck_subscribe(openid: str, scene: str) -> tuple[bool, str, str | None]:
    """订阅推送前置判定（**单一事实源**）：`(可入队, 模板ID 或 真实原因, 状态提示)`。

    为什么单列一个函数（P2-42，2026-10-20 第八轮）：这套判定原先写死在
    `push_subscribe_message` 里，调用方 `NotificationService._wechat_push` 又**另抄一份**
    "通道未启用 / 无 openid"的判定 → 同一口径两处实现（改一处漏一处）。
    现在调用方直接调本函数取 `(状态, 原因)`，`push_subscribe_message` 也走它。
    """
    from backend.config import get_settings

    if not get_settings().WECHAT_SUBSCRIBE_ENABLED:
        return False, "通道未启用", "skipped"
    if not openid:
        return False, "家长未授权（无 openid）", "skipped"
    attr, template_id = _scene_template(scene)
    if not attr:
        return False, f"场景未映射订阅模板（{scene or '未传 scene'}）——仅站内消息", "skipped"
    if not template_id:
        return False, f"订阅模板 {attr} 未配置（微信后台申请后填入 .env）", "skipped"
    return True, template_id, None


def _runner(
    openid: str,
    template_id: str,
    title: str,
    content: str,
    on_result: Callable[..., None] | None,
) -> None:
    """线程池工作体：发一条订阅消息，把**真实结果**回调给调用方。

    **必须**在 finally 释放积压名额——漏了就会把队列越用越小（永久性误报"队列已满"）。
    """
    ok, error, status = False, "", None
    try:
        result = asyncio.run(
            WeChatSubscribe.send(openid, template_id, _build_template_data(title, content))
        )
        ok, error, status = classify_send_result(result)
        if status == "skipped":
            logger.info(f"Subscribe push skipped(43101): template={template_id}")
        else:
            logger.info(f"Subscribe push ok: template={template_id}, title={title[:20]}")
    except Exception as exc:  # noqa: BLE001 — 网络失败不阻塞主流程，但结果要如实回报
        ok, error = False, f"微信订阅发送失败: {str(exc)[:200]}"
        logger.exception(f"Subscribe push failed: title={title[:20]}")
    finally:
        _PUSH_BACKLOG.release()
    if on_result is not None:
        try:
            on_result(ok, error, status)
        except Exception:  # noqa: BLE001 — 回写失败只记日志，不影响已完成的发送
            logger.exception("Subscribe push on_result 回调失败")


def push_subscribe_message(
    openid: str,
    title: str,
    content: str,
    scene: str = "",
    on_result: Callable[..., None] | None = None,
) -> tuple[bool, str, str | None]:
    """SystemMessage 落库后调用 — 排队微信订阅消息推送。

    返回 `(是否已入队, 原因/说明, 状态提示)`（P2-42，2026-10-20 第八轮补第三项）：
    - **未入队**（False）：`原因` 是**真实原因**，`状态提示` 是**该写库的状态**——
      `"skipped"` = 主动跳过（通道未启用 / 无 openid / 场景未映射 / 模板未配置）；
      `"failed"` = 本该发但没发成（推送队列已满 / 线程池不可用）。两者都不许伪装成"已送达"；
    - **已入队**（True）：**"已提交"不等于"已送达"**——发送在线程池 worker 里执行 asyncio.run
      （避免在请求/事件循环线程内冲突），真实结果通过 `on_result(ok, error, status)` 回调告知
      （调用方在 worker 线程里用独立 session 回写 `sent`/`failed`/`skipped`）。

    P2-42（2026-10-20 第八轮）：**并发有界**。原先每条通知起一个 daemon 线程（无上限），
    千条提醒 = 千线程 + 千连接；现在固定 `PUSH_WORKERS` 个 worker，积压超 `PUSH_QUEUE_MAX`
    时如实返回"队列已满"（调用方记 failed + 原因），不静默丢弃、不无限排队。
    """
    ok, info, hint = precheck_subscribe(openid, scene)
    if not ok:
        return False, info, hint
    template_id = info
    if not _PUSH_BACKLOG.acquire(blocking=False):
        return False, f"推送队列已满（上限 {PUSH_QUEUE_MAX} 条待发），本条未提交微信", "failed"
    try:
        _PUSH_POOL.submit(_runner, openid, template_id, title, content, on_result)
    except RuntimeError as exc:  # 线程池已关闭（进程退出中）
        _PUSH_BACKLOG.release()
        return False, f"推送线程池不可用（{str(exc)[:80]}）", "failed"
    attr, _tid = _scene_template(scene)
    logger.info(f"Subscribe push queued: template={template_id}, title={title[:20]}")
    return True, f"已提交微信（模板 {attr}）", None


def _build_template_data(title: str, content: str) -> dict:
    """构建订阅消息模板字段

    thing1=标题 / thing2=内容 / time3=时间 为微信订阅消息最常见字段结构；
    实际模板字段名因模板而异，模板申请后若字段不符在此微调。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return {
        "thing1": {"value": title[:20]},
        "thing2": {"value": content[:20]},
        "time3": {"value": now},
    }


class WeChatSubscribe:
    """微信订阅消息 — 替代已废弃的模板消息"""

    SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"

    @staticmethod
    async def send(openid: str, template_id: str, data: dict, page: str = "") -> dict:
        """
        发送订阅消息

        参数：
          openid: 用户 openid
          template_id: 模板 ID（在微信后台配置）
          data: 模板数据，格式 {"key1": {"value": "xxx"}, "key2": {"value": "yyy"}}
          page: 点击消息后跳转的小程序页面

        Raises:
            ValidationError: 参数错误（如模板 ID 无效）
            PaymentError: 微信接口返回系统错误
        """
        if not template_id:
            logger.warning("Subscribe message skipped: template_id is empty")
            return {"errcode": -1, "errmsg": "template_id not configured"}

        token_data = await _get_access_token()
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise PaymentError("获取微信 access_token 失败")

        body = {
            "touser": openid,
            "template_id": template_id,
            "data": data,
        }
        if page:
            body["page"] = page

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{WeChatSubscribe.SEND_URL}?access_token={access_token}",
                json=body,
            )
            result = resp.json()

        errcode = result.get("errcode", 0)
        if errcode != 0:
            errmsg = result.get("errmsg", "未知错误")
            if errcode in (40003, 41028, 41029, 41030):
                raise ValidationError(f"订阅消息发送失败: {errmsg}")
            elif errcode == 43101:
                # F-041：openid 日志脱敏（仅保留后 4 位）
                logger.info(f"User ...{openid[-4:]} refused subscribe message")
                return result
            else:
                logger.error(f"Subscribe message failed: errcode={errcode}, errmsg={errmsg}")
                raise PaymentError(f"订阅消息发送失败: {errmsg}")

        return result


async def _get_access_token() -> dict:
    """获取微信 access_token — 委托 WeChatService 统一管理

    与 /wechat/qr-code 共享同一份带双重检查锁定的缓存，
    避免多套 token 管理机制并发刷新导致相互失效。
    """
    from backend.integrations.wechat.service import WeChatService

    service = WeChatService()
    try:
        token = await asyncio.to_thread(service.get_access_token)
        return {"access_token": token}
    except Exception as e:
        logger.error(f"获取微信 access_token 失败: {e}", exc_info=True)
        return {}
