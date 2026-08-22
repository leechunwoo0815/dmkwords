// activities API（WM9：发布/取消/报名/签到/退款审核）
import { request } from "./client";

export interface ActivityItem {
  id: number;
  title: string;
  activity_type: string;
  start_at: string;
  location: string;
  fee: string;
  fee_display: string;
  member_only: boolean;
  enroll_deadline: string | null;
  status: string;
  quota_used: number;
  quota_left: number;
  max_quota: number;
  full: boolean;
  description: string | null;
}

export interface EnrollmentItem {
  id?: number;
  enrollment_id?: number;
  activity_id: number;
  child_id: number;
  child_name?: string;
  status: string;
  ticket_code: string;
  checked_in_at: string | null;
  created_at: string;
  activity_title?: string;
  amount?: string;
  reason?: string;
}

export interface CreateActivityBody {
  title: string;
  activity_type: string;
  start_at: string;
  location: string;
  max_quota: number;
  fee: number;
  description?: string;
  member_only?: boolean;
  enroll_deadline?: string;
}

export function apiListActivities(status?: string): Promise<ActivityItem[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/activities${qs}`);
}

export function apiCreateActivity(body: CreateActivityBody): Promise<{ id: number }> {
  return request("/api/admin/activities", { method: "POST", body: JSON.stringify(body) });
}

export function apiCancelActivity(id: number): Promise<{
  refund_pending: number; cancelled: number;
}> {
  return request(`/api/admin/activities/${id}/cancel`, { method: "POST" });
}

export function apiListEnrollments(activityId: number): Promise<EnrollmentItem[]> {
  return request(`/api/admin/activities/${activityId}/enrollments`);
}

export function apiSignin(ticketCode: string): Promise<{ enrollment_id: number; checked_in_at: string }> {
  return request("/api/admin/activity-signin", {
    method: "POST", body: JSON.stringify({ ticket_code: ticketCode }),
  });
}

export function apiListActivityRefunds(): Promise<EnrollmentItem[]> {
  return request("/api/admin/activity-refunds");
}

export function apiReviewActivityRefund(
  enrollmentId: number, approve: boolean, remark: string,
): Promise<{ enrollment_id: number; status: string }> {
  return request(`/api/admin/activity-refunds/${enrollmentId}/review`, {
    method: "POST", body: JSON.stringify({ approve, remark }),
  });
}
