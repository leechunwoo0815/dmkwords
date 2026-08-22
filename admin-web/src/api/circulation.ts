// circulation API
import { request } from "./client";
import type { components } from "./schema";

export type ChildCard = components["schemas"]["ChildCardResponse"];
export type BorrowRecord = components["schemas"]["BorrowRecordResponse"];
export type OverdueItem = components["schemas"]["OverdueItemResponse"];

export function apiChildCard(childId: number): Promise<ChildCard> {
  return request(`/api/admin/circulation/children/${childId}/card`);
}

export function apiBorrow(body: {
  child_id: number; isbn?: string; copy_id?: number; override_reason?: string;
}): Promise<BorrowRecord> {
  return request("/api/admin/circulation/borrow", { method: "POST", body: JSON.stringify(body) });
}

export function apiReturnBook(copy_id: number, condition: string): Promise<BorrowRecord> {
  return request("/api/admin/circulation/return", { method: "POST", body: JSON.stringify({ copy_id, condition }) });
}

export function apiRenew(record_id: number): Promise<BorrowRecord> {
  return request("/api/admin/circulation/renew", { method: "POST", body: JSON.stringify({ record_id }) });
}

export function apiOverdueList(): Promise<OverdueItem[]> {
  return request("/api/admin/circulation/overdue");
}

// 搜索孩子（复用 members API）
export { apiListChildren } from "./members";
