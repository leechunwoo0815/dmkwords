// identity/members 域 API
import { request } from "./client";
import type { components } from "./schema";

export type Child = components["schemas"]["ChildWithParentResponse"];
export type Order = components["schemas"]["OrderResponse"];
type PaginatedChildren = components["schemas"]["PaginatedResponse_ChildWithParentResponse_"];
type PaginatedOrders = components["schemas"]["PaginatedResponse_OrderResponse_"];

export function apiListChildren(params: {
  page: number; page_size: number; keyword?: string; status?: string;
}): Promise<PaginatedChildren> {
  const q = new URLSearchParams({ page: String(params.page), page_size: String(params.page_size) });
  if (params.keyword) q.set("keyword", params.keyword);
  if (params.status) q.set("status", params.status);
  return request(`/api/admin/members/children?${q.toString()}`);
}

export function apiCreateParent(body: { name: string; phone: string; remark?: string }): Promise<{ id: number }> {
  return request("/api/admin/members/parents", { method: "POST", body: JSON.stringify(body) });
}

export function apiCreateChild(parentId: number, body: {
  name: string; english_name?: string; gender?: number; birthday?: string; grade?: string;
}): Promise<{ id: number }> {
  return request(`/api/admin/members/parents/${parentId}/children`, { method: "POST", body: JSON.stringify(body) });
}

export function apiListOrders(params: {
  page: number; page_size: number; status?: string; keyword?: string;
}): Promise<PaginatedOrders> {
  const q = new URLSearchParams({ page: String(params.page), page_size: String(params.page_size) });
  if (params.status) q.set("status", params.status);
  if (params.keyword) q.set("keyword", params.keyword);
  return request(`/api/admin/orders?${q.toString()}`);
}

export function apiCreateOrder(body: { child_id: number; order_type: string; remark?: string }): Promise<Order> {
  return request("/api/admin/orders", { method: "POST", body: JSON.stringify(body) });
}

export function apiConfirmPayment(orderId: number, body: { pay_method: string; remark?: string }): Promise<Order> {
  return request(`/api/admin/orders/${orderId}/confirm-payment`, { method: "POST", body: JSON.stringify(body) });
}

export function apiCancelOrder(orderId: number): Promise<Order> {
  return request(`/api/admin/orders/${orderId}/cancel`, { method: "POST" });
}

export function apiRefundOrder(orderId: number, remark: string): Promise<{ id: number; status: string }> {
  return request(`/api/admin/orders/${orderId}/refund`, {
    method: "POST", body: JSON.stringify({ remark }),
  });
}

// C13：观察期 → 待评估（馆员手动标记，留痕）
export function apiMarkPendingEvaluation(childId: number, reason: string): Promise<Child> {
  return request(`/api/admin/members/children/${childId}/mark-pending-evaluation`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function apiUpdateChild(
  childId: number,
  body: { english_name?: string; grade?: string; ar_level?: string },
): Promise<Child> {
  return request(`/api/admin/members/children/${childId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
// C13：评估通过转正（创建年费订单，收款确认后转正式会员）
export function apiEvaluateApprove(childId: number, reason: string): Promise<Order> {
  return request(`/api/admin/members/children/${childId}/evaluate-approve`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
}
