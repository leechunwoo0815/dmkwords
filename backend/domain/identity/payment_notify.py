# backend/domain/identity/payment_notify.py — 支付/退款回调处理与异常留痕（从 payment_service 抽出）
"""为什么单开：`payment_service.py` 触及架构关 god-file 上限（>800 行）。

这里放的都是**回调侧**的自洽一组：报文提示提取、信封解析与解密、退款结果通知处理、异常留痕
（含 2026-10-12 拍板 P10 的"资金异常进管理端待办"）。以 mixin 形式挂回 `PaymentService`，
`self.db` / `self._audit_anomaly` 等引用原样成立。
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from backend.common.async_utils import run_coro
from backend.domain.catalog.audit_events import publish_audit

logger = logging.getLogger(__name__)


class PaymentNotifyMixin:
    """回调处理与留痕（宿主类提供 `db` 与网关方法）。"""

    def _handle_refund_notify(self, gateway, envelope: dict, event_type: str) -> tuple[int, dict]:
        """退款结果通知（WM12-B）：`REFUND.SUCCESS` 落已退款 / `CLOSED|ABNORMAL` 记失败 / 其余保持执行中。

        幂等与乱序守卫在 `refund_online.finalize_gateway_result`（已退款单的重复/迟到失败通知不改状态）。
        """
        from backend.domain.identity import refund_online
        from backend.domain.identity.wm10_service import RefundService

        data = self._decrypt(gateway, envelope)
        if data is None:
            self._audit_anomaly("退款回调解密失败", reason=event_type)
            return 400, {"code": "FAIL", "message": "回调数据无法解密"}
        out_refund_no = (data.out_refund_no or "").strip()
        if not out_refund_no:
            self._audit_anomaly("退款回调缺商户退款单号", reason=event_type)
            return 400, {"code": "FAIL", "message": "缺少退款单号"}
        req = refund_online.find_by_out_refund_no(self.db, out_refund_no)
        # 2026-10-12 审查 P2（T2）：**反解到单 ≠ 这笔通知属于当前提交**。换号重试后
        # （RF{id}-2-1），旧号（RF{id}-1-1）的迟到通知会落到"正在进行中的那一笔"上：
        # 旧笔 CLOSED 会把在途单判成失败（管理员据此换号重提 = 二次出款），旧笔 SUCCESS
        # 会把在途单直接置为已退款（真在途那笔的成功回调被幂等忽略 → 账实不符）。
        # 只在"这一笔确实提交过"（有单号）时才比对：未提交的单没有"当前提交"可比，
        # 交给原有状态机与字段校验处理（A1 的"缺金额→400"用例即此路径）。
        if (req.out_refund_no or "").strip() and req.out_refund_no.strip() != out_refund_no.strip():
            self._audit_anomaly(
                "退款结果通知的单号与当前提交不一致（已忽略，防旧通知落错笔）",
                target_id=f"refund_request#{req.id}",
                reason=f"通知={out_refund_no} 当前={req.out_refund_no}",
            )
            return 200, {"code": "SUCCESS", "message": "已忽略（单号非当前提交）"}
        if req is None:
            self._audit_anomaly("退款回调找不到退款单", reason=f"out_refund_no={out_refund_no}")
            return 404, {"code": "FAIL", "message": "退款单不存在"}

        finalize = RefundService(self.db)._finalize_refund
        state = (data.refund_status or "").upper()
        if state == "SUCCESS":
            # WM12-C（审查 P1-7）：退款回调必须与**申请金额**比对——"微信只退了 10 元、
            # 本地把整单记成已退款"这类不一致，原先完全没人发现（直接照抄回调的结果）。
            # 不符 → 不落终态 + 审计 + 400（让人工核对；照抄微信金额的代价是本地账实不符）。
            if data.refund_amount is None:
                # P1-14（2026-10-09）：**缺金额即拒**——与支付侧同口径（支付回调缺金额直接拒）。
                # 原实现只留一条 anomaly 后继续按 `req.amount` 落"已退款"：微信字段改名或
                # 部分退款场景下，本地会把整笔记成已退、订单转 refunded，账实不符。
                self._audit_anomaly(
                    "退款回调未携带退款金额，拒绝落终态（如实处置）",
                    target_id=str(req.id),
                    reason=f"out_refund_no={out_refund_no}",
                )
                return 400, {"code": "FAIL", "message": "退款回调缺退款金额"}
            elif Decimal(data.refund_amount).quantize(Decimal("0.01")) != Decimal(
                req.amount
            ).quantize(Decimal("0.01")):
                self._audit_anomaly(
                    "退款回调金额与申请金额不符，拒绝落终态",
                    target_id=str(req.id),
                    reason=(
                        f"回调={data.refund_amount} 申请={req.amount} out_refund_no={out_refund_no}"
                    ),
                )
                return 400, {"code": "FAIL", "message": "退款金额不符"}
            result = refund_online.finalize_gateway_result(
                self.db,
                req.id,
                success=True,
                remark=f"微信退款到账（{out_refund_no}）",
                finalize=finalize,
            )
            ignored = bool(result.get("ignored"))
            if not ignored:
                message = "已退款"
            elif result.get("reason") == "state_not_processable":
                message = "状态不可推进，已忽略并留痕"
            else:
                message = "重复通知已忽略"
            return 200, {"code": "SUCCESS", "message": message}
        if state in ("CLOSED", "ABNORMAL"):
            result = refund_online.finalize_gateway_result(
                self.db,
                req.id,
                success=False,
                remark=f"微信退款未成功（{state}，{out_refund_no}）",
                finalize=finalize,
            )
            ignored = bool(result.get("ignored"))
            if not ignored:
                message = "已记失败"
            elif result.get("reason") == "state_not_processable":
                message = "状态不可推进，已忽略并留痕"
            else:
                message = "重复通知已忽略"
            return 200, {"code": "SUCCESS", "message": message}
        # PROCESSING 等中间态：保持"执行中"，等下一次通知（不 commit 会丢审计，所以显式提交）
        self.db.commit()
        return 200, {"code": "SUCCESS", "message": "处理中，等待后续通知"}

    # ---------- 开发/验收通道 ----------

    @staticmethod
    def _notify_hint(body: str) -> dict:
        """从回调报文里**尽力**提取可定位字段（解不开密文时也能人工核对）。"""
        import hashlib
        import json
        import re

        out: dict[str, str] = {}
        raw = body or ""
        out["digest"] = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        try:
            data = json.loads(raw or "{}")
            if isinstance(data, dict):
                out["event_type"] = str(data.get("event_type", "") or "")
                out["notify_id"] = str(data.get("id", "") or "")
        except (ValueError, TypeError):
            pass
        m = re.search(r'"out_trade_no"\s*:\s*"([A-Za-z0-9_\-]{4,64})"', raw)
        if m:
            out["out_trade_no"] = m.group(1)
        m2 = re.search(r'"out_refund_no"\s*:\s*"([A-Za-z0-9_\-]{4,64})"', raw)
        if m2:
            out["out_refund_no"] = m2.group(1)
            out.setdefault("out_trade_no", m2.group(1))
        return out

    @staticmethod
    def _parse_envelope(body: str) -> dict | None:
        try:
            envelope = json.loads(body)
        except (TypeError, ValueError):
            return None
        return envelope if isinstance(envelope, dict) else None

    @staticmethod
    @staticmethod
    def _decrypt(gateway, envelope: dict):
        resource = envelope.get("resource") or {}
        ciphertext = str(resource.get("ciphertext") or "")
        if not ciphertext:
            return None
        try:
            return run_coro(
                gateway.decrypt_callback_data(
                    ciphertext,
                    str(resource.get("nonce") or ""),
                    str(resource.get("associated_data") or ""),
                )
            )
        except Exception as exc:
            logger.error("支付回调解密失败: %s", exc, exc_info=True)
            return None

    def _audit_anomaly(
        self,
        message: str,
        *,
        target_id: str = "",
        reason: str = "",
        admin=None,
    ) -> None:
        """异常/演练留痕（**不抛错**：回调路径的任何异常都不能变成"无应答"）。

        资金相关异常必须留痕，否则出问题时只能看到"钱少了"而查不到经过。
        """
        logger.warning("支付异常留痕: %s | %s", message, reason)
        # 2026-10-12 拍板（P10）：**资金异常要进管理端待办**。原先只写审计行，而审计页仅超管可见
        # ——"钱收了没入账"这类事故只能靠人主动翻页面。这里同事务补一条待办（失败不影响留痕）。
        try:
            from backend.common.admin_notification_models import AdminNotification
            from backend.common.admin_notifications import AdminNotifyService

            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_PAYMENT_ANOMALY,
                title="【资金异常】",
                content=f"【资金异常】{message}｜{reason[:160]}",
                ref_type=AdminNotification.REF_CHILD,
                ref_id=target_id or "-",
                applicant_name="系统",
            )
        except Exception:  # noqa: BLE001 —— 待办失败不能反过来影响资金留痕
            logger.exception("资金异常待办写入失败（审计已留痕）")
        publish_audit(
            self.db,
            admin=admin or self._system_actor(),
            action="payment.anomaly",
            target_type="order",
            target_id=target_id or "-",
            detail={"message": message},
            reason=reason,
        )
        self.db.commit()
