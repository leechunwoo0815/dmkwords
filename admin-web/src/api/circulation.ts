// circulation API
import { downloadExcel } from "./admin";
import { request } from "./client";
import type { components } from "./schema";

export type ChildCard = components["schemas"]["ChildCardResponse"];
export type BorrowRecordResponse = components["schemas"]["BorrowRecordResponse"];
export type BorrowRecord = components["schemas"]["BorrowRecordResponse"];
export type OverdueItem = components["schemas"]["OverdueItemResponse"];
export type ScanResult = components["schemas"]["ScanResponse"];
export type BorrowRecordItem = components["schemas"]["RecordItemResponse"];

export interface BorrowRecordQuery {
  page?: number;
  page_size?: number;
  keyword?: string;
  status?: string;
  date_field?: "borrowed" | "returned";
  date_from?: string;
  date_to?: string;
}

function toQuery(q: BorrowRecordQuery): string {
  const p = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  return p.toString();
}

// 借还记录查询（2026-09-21 D 批）
export function apiBorrowRecords(
  q: BorrowRecordQuery,
): Promise<{ items: BorrowRecordItem[]; total: number }> {
  return request(`/api/admin/circulation/records?${toQuery(q)}`);
}

// 导出**当前筛选结果**（后端按同一组参数生成 xlsx）
export function apiExportBorrowRecords(q: BorrowRecordQuery): Promise<void> {
  return downloadExcel(`/api/admin/circulation/records/export?${toQuery(q)}`, "borrow-records.xlsx");
}

export function apiChildCard(childId: number): Promise<ChildCard> {
  return request(`/api/admin/circulation/children/${childId}/card`);
}

// 按会员码取卡片（借阅台"扫会员码"框；2026-09-21 C 批）
export function apiChildCardByCode(memberCode: string): Promise<ChildCard> {
  return request(`/api/admin/circulation/children/by-code/${encodeURIComponent(memberCode)}/card`);
}

// 扫码统一入口：扫 ISBN → 自动判借/还/核销（2026-09-21 C 批）
export function apiScan(child_id: number, code: string): Promise<ScanResult> {
  return request("/api/admin/circulation/scan", {
    method: "POST",
    body: JSON.stringify({ child_id, code }),
  });
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
