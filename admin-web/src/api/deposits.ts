// billing/deposits API
import { request } from "./client";
import type { components } from "./schema";

export type Deposit = components["schemas"]["DepositResponse"];
export type DepositLedger = components["schemas"]["DepositLedgerResponse"];
type PaginatedDeposits = components["schemas"]["PaginatedResponse_DepositResponse_"];

export function apiListDeposits(params: { page: number; page_size: number; status?: string; keyword?: string }): Promise<PaginatedDeposits> {
  const q = new URLSearchParams({ page: String(params.page), page_size: String(params.page_size) });
  if (params.status) q.set("status", params.status);
  if (params.keyword) q.set("keyword", params.keyword);
  return request(`/api/admin/deposits?${q.toString()}`);
}

export function apiGetDeposit(childId: number): Promise<Deposit | null> {
  return request(`/api/admin/deposits/children/${childId}`);
}

export function apiGetDepositLedgers(childId: number): Promise<DepositLedger[]> {
  return request(`/api/admin/deposits/children/${childId}/ledgers`);
}

export function apiCreateDepositOrder(childId: number): Promise<{ order_id: number; order_no: string; amount: string; status: string }> {
  return request(`/api/admin/deposits/children/${childId}/orders`, { method: "POST" });
}

export function apiCreateSupplementOrder(childId: number): Promise<{ order_id: number; order_no: string; amount: string; status: string }> {
  return request(`/api/admin/deposits/children/${childId}/supplement-orders`, { method: "POST" });
}

export function apiDeductDeposit(childId: number, body: { amount: string; reason: string; copy_id?: number }): Promise<Deposit> {
  return request(`/api/admin/deposits/children/${childId}/deduct`, { method: "POST", body: JSON.stringify(body) });
}
