# backend/integrations/wechat/subscribe.py
"""微信订阅消息集成 — access_token 委托 WeChatService 统一管理"""

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime

import httpx

from backend.common.exceptions import PaymentError, ValidationError
from backend.integrations.wechat.config import SubscribeTemplate

logger = logging.getLogger(__name__)

# 高价值消息标题 → 模板映射（订阅消息为"一次授权一次推送"稀缺通道，只推需及时触达的消息；
# 标题未命中或模板未配置 → 降级只落库，不推送）
_TITLE_TEMPLATE_RULES: list[tuple[str, str]] = [
    ("会员续费提醒", "MEMBER_EXPIRE_REMIND"),
    ("借阅到期提醒", "RETURN_REMIND"),
    ("预约取书提醒", "RESERVATION_READY"),
    ("预约已过期", "RESERVATION_EXPIRING"),
    ("您等候的图书到货啦", "RESERVATION_READY"),
    ("活动取消通知", "ACTIVITY_REMIND"),
    ("退款审核通过", "REFUND_RESULT"),
    ("退款审核拒绝", "REFUND_RESULT"),
    ("图书损坏通知", "LEVEL_UP"),  # 复用晋级通知模板位，按实际申请调整
]


def get_template_for_title(title: str) -> str:
    """按消息标题匹配订阅模板 ID（未配置/未命中返回空串 → 调用方降级）"""
    for keyword, attr in _TITLE_TEMPLATE_RULES:
        if keyword in title:
            return getattr(SubscribeTemplate, attr, "") or ""
    return ""


def push_subscribe_message(
    openid: str,
    title: str,
    content: str,
    on_result: Callable[[bool, str], None] | None = None,
) -> tuple[bool, str]:
    """SystemMessage 落库后调用 — 排队微信订阅消息推送。

    返回 `(是否已入队, 原因/说明)`（P2-24，2026-10-17 第六十九批·大审查）：
    - **未入队**（False）：条件不满足，`原因` 是**真实原因**（通道未启用 / 标题未命中模板映射 /
      模板未配置）——调用方据此写 `skipped` 并展示原因，不许再伪装成"发送失败（额度不足/用户拒收）"；
    - **已入队**（True）：**"已提交"不等于"已送达"**——发送在 daemon 线程执行 asyncio.run
      （避免在请求/事件循环线程内冲突），真实结果通过 `on_result(ok, error)` 回调告知
      （调用方在后台线程里用独立 session 回写 `sent`/`failed`）。

    条件：总开关开启（WECHAT_SUBSCRIBE_ENABLED）+ openid 有效 + 标题命中高价值映射
    + 模板 ID 已配置。网络失败/微信拒发均不阻塞主流程。
    """
    from backend.config import get_settings

    if not get_settings().WECHAT_SUBSCRIBE_ENABLED:
        return False, "通道未启用"
    if not openid:
        return False, "家长未授权（无 openid）"
    attr = _match_template_attr(title)
    if not attr:
        return False, f"标题未命中订阅模板映射（{title[:16]}）——仅站内消息"
    template_id = getattr(SubscribeTemplate, attr, "") or ""
    if not template_id:
        return False, f"订阅模板 {attr} 未配置（微信后台申请后填入 .env）"

    def _runner():
        try:
            asyncio.run(
                WeChatSubscribe.send(openid, template_id, _build_template_data(title, content))
            )
            ok, error = True, ""
            logger.info(f"Subscribe push ok: template={template_id}, title={title[:20]}")
        except Exception as exc:  # noqa: BLE001 — 网络失败不阻塞主流程，但结果要如实回报
            ok, error = False, f"微信订阅发送失败: {str(exc)[:200]}"
            logger.exception(f"Subscribe push failed: title={title[:20]}")
        if on_result is not None:
            try:
                on_result(ok, error)
            except Exception:  # noqa: BLE001 — 回写失败只记日志，不影响已完成的发送
                logger.exception("Subscribe push on_result 回调失败")

    threading.Thread(target=_runner, daemon=True).start()
    logger.info(f"Subscribe push queued: template={template_id}, title={title[:20]}")
    return True, f"已提交微信（模板 {attr}）"


def _match_template_attr(title: str) -> str:
    """按标题匹配模板 attr（未命中返回空串）——与 `get_template_for_title` 共用同一份映射表。"""
    for keyword, attr in _TITLE_TEMPLATE_RULES:
        if keyword in title:
            return attr
    return ""


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
