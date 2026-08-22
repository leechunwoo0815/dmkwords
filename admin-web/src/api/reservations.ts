// reservations API（WM6：预约管理 / 核销 / 孩子阅读档案）
import { request } from "./client";

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

export function apiListReservations(status?: string): Promise<ReservationItem[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/reservations${qs}`);
}

export function apiCheckoutReservation(reservationId: number): Promise<CheckOutResult> {
  return request(`/api/admin/reservations/${reservationId}/checkout`, { method: "POST" });
}

export function apiGetChildReading(childId: number): Promise<ChildReadingProfile> {
  return request(`/api/admin/children/${childId}/reading`);
}
