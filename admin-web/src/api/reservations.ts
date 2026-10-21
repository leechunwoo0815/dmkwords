// reservations API（WM6：预约管理 / 核销 / 孩子阅读档案）
import { ListResult, request, requestList } from "./client";

export interface ReservationItem {
  id: number;
  child_id: number;
  child_name: string;
  parent_name: string;
  parent_phone: string;
  book_id: number;
  book_title: string;
  copy_id: number;
  status: string;
  created_at: string;
  expires_at: string;
  expired: boolean;
}

export interface CheckOutResult {
  reservation_id: number;
  borrow_record_id: number;
  due_at: string;
}

export interface FinishedBook {
  book_id: number;
  title: string;
  author: string | null;
  word_count: number | null;
  finished_at: string;
  reading_minutes: number;
}

export interface ChildReadingProfile {
  child_id: number;
  child_name: string;
  member_status: string;
  total_finished: number;
  total_reading_minutes: number;
  total_checkin_days: number;
  current_streak: number;
  finished_books: FinishedBook[];
}

// P2-44：预约列表后端硬上限 200——返回上限元信息，页面据此提示"可能还有更多"。
export function apiListReservations(
  status?: string, keyword?: string,
): Promise<ListResult<ReservationItem[]>> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  // 2026-10-12 审查 P1：**不要预编码**——URLSearchParams.toString() 会再编码一次，
  // 中文关键词会变成 `%25E5%25BC%25A0` 字面量传给后端 → 模糊匹配必然为空（搜索框形同失效）。
  if (keyword) params.set("keyword", keyword);
  const qs = params.toString();
  return requestList(`/api/admin/reservations${qs ? `?${qs}` : ""}`);
}

export function apiCheckoutReservation(reservationId: number): Promise<CheckOutResult> {
  return request(`/api/admin/reservations/${reservationId}/checkout`, { method: "POST" });
}

export function apiGetChildReading(childId: number): Promise<ChildReadingProfile> {
  return request(`/api/admin/children/${childId}/reading`);
}
