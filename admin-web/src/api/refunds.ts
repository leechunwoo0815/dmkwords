// refunds API（WM10：退款/退会/转让审核 + 评估报告）
import { ListResult, request, requestList } from "./client";

export interface RefundRequestItem {
  id: number;
  kind: string;
  order_id: number | null;
  child_id: number;
  child_name: string;
  amount: string;
  reason: string;
  status: string;
  review_remark: string | null;
  order_no?: string;
  order_type?: string;
  pay_method?: string;
  /** WM12-B：执行时会走微信原路退回（wechat）还是线下打款登记（offline） */
  refund_channel?: string;
  /** WM12-B：商户退款单号（线上原路退款的幂等键，仅在途/已退时有值） */
  out_refund_no?: string;
  /** WM12-C：网关结果未知（超时）的标记时间——非空=禁止重提，先「查单」确认 */
  gateway_unknown_at?: string;
  created_at: string;
}

export interface WithdrawalItem {
  id: number;
  child_id: number;
  child_name: string;
  member_status: string;
  reason: string;
  status: string;
  source?: string; // R4：联动判定（refund_linked/transfer_linked≠normal）
  review_remark: string | null;
  created_at: string;
}

export interface TransferItem {
  id: number;
  source_child_id: number;
  source_name: string;
  target_child_id: number;
  target_name: string;
  status: string;
  expires_at: string;
  review_remark: string | null;
  created_at: string;
}

// P2-44：三个台账都是"最近 N 条"硬上限（200）——返回上限元信息，页面据此提示
// "可能还有更多未显示"（原先 200 条看起来就是全部，待审单会被漏看）。
export interface RefundCenterCounts {
  refund: Record<string, number>;
  withdrawal: Record<string, number>;
  transfer: Record<string, number>;
}

/** P1-7：退款中心三个台账的按状态计数（服务端分页后徽标必须来自后端，不能只数本页）。 */
export function apiRefundCenterCounts(): Promise<RefundCenterCounts> {
  return request("/api/admin/refund-center/counts");
}

function pageQs(page?: number, pageSize?: number): string {
  const ps = new URLSearchParams();
  if (page) ps.set("page", String(page));
  if (pageSize) ps.set("page_size", String(pageSize));
  return ps.toString();
}

export function apiListRefunds(
  status?: string, page?: number, pageSize?: number,
): Promise<ListResult<RefundRequestItem[]>> {
  const ps = new URLSearchParams();
  if (status) ps.set("status", status);
  const q = pageQs(page, pageSize);
  const qs = [ps.toString(), q].filter(Boolean).join("&");
  return requestList(`/api/admin/refund-requests${qs ? `?${qs}` : ""}`);
}

export function apiReviewRefund(
  id: number, approve: boolean, remark: string,
): Promise<{ id: number; status: string }> {
  return request(`/api/admin/refund-requests/${id}/review`, {
    method: "POST", body: JSON.stringify({ approve, remark }),
  });
}

export function apiExecuteRefund(
  id: number, success: boolean, remark: string,
): Promise<{ id: number; status: string }> {
  return request(`/api/admin/refund-requests/${id}/execute`, {
    method: "POST", body: JSON.stringify({ success, remark }),
  });
}

/** WM12-C（审查 P0-1）：退款查单——未知态（网关超时）确认微信侧结果后才允许重试。 */
export function apiQueryRefundGateway(id: number): Promise<{
  id: number; status: string; gateway_state: string; resolved: boolean; note: string;
}> {
  return request(`/api/admin/refund-requests/${id}/query-gateway`, { method: "POST" });
}

export function apiListWithdrawals(
  status?: string, page?: number, pageSize?: number,
): Promise<ListResult<WithdrawalItem[]>> {
  const ps = new URLSearchParams();
  if (status) ps.set("status", status);
  const q = pageQs(page, pageSize);
  const qs = [ps.toString(), q].filter(Boolean).join("&");
  return requestList(`/api/admin/withdrawals${qs ? `?${qs}` : ""}`);
}

export function apiReviewWithdrawal(
  id: number, approve: boolean, remark: string,
): Promise<{ id: number; status: string }> {
  return request(`/api/admin/withdrawals/${id}/review`, {
    method: "POST", body: JSON.stringify({ approve, remark }),
  });
}

export interface SettlePreviewItem {
  kind: string;
  order_no: string | null;
  amount: string;
  rule: string;
}

export interface SettlePreview {
  items: SettlePreviewItem[];
  deposit_balance: string;
  total: string;
}

export function apiSettlePreview(id: number): Promise<SettlePreview> {
  return request(`/api/admin/withdrawals/${id}/settle-preview`);
}

export function apiListTransfers(
  status?: string, page?: number, pageSize?: number,
): Promise<ListResult<TransferItem[]>> {
  const ps = new URLSearchParams();
  if (status) ps.set("status", status);
  const q = pageQs(page, pageSize);
  const qs = [ps.toString(), q].filter(Boolean).join("&");
  return requestList(`/api/admin/transfers${qs ? `?${qs}` : ""}`);
}

export function apiReviewTransfer(
  id: number, approve: boolean, remark: string,
): Promise<{ id: number; status: string }> {
  return request(`/api/admin/transfers/${id}/review`, {
    method: "POST", body: JSON.stringify({ approve, remark }),
  });
}
