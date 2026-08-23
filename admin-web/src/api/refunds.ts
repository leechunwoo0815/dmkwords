// refunds API（WM10：退款/退会/转让审核 + 评估报告）
import { request } from "./client";

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
  created_at: string;
}

export interface WithdrawalItem {
  id: number;
  child_id: number;
  child_name: string;
  member_status: string;
  reason: string;
  status: string;
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

export function apiListRefunds(status?: string): Promise<RefundRequestItem[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/refund-requests${qs}`);
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

export function apiListWithdrawals(status?: string): Promise<WithdrawalItem[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/withdrawals${qs}`);
}

export function apiReviewWithdrawal(
  id: number, approve: boolean, remark: string,
): Promise<{ id: number; status: string }> {
  return request(`/api/admin/withdrawals/${id}/review`, {
    method: "POST", body: JSON.stringify({ approve, remark }),
  });
}

export function apiListTransfers(status?: string): Promise<TransferItem[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/transfers${qs}`);
}

export function apiReviewTransfer(
  id: number, approve: boolean, remark: string,
): Promise<{ id: number; status: string }> {
  return request(`/api/admin/transfers/${id}/review`, {
    method: "POST", body: JSON.stringify({ approve, remark }),
  });
}
