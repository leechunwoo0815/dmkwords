// activities API（WM9：发布/取消/报名/签到/退款审核）
import { getToken, request } from "./client";

export interface ActivityItem {
  cover_url?: string | null;
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

export function apiListActivities(params?: {
  status?: string;
  keyword?: string;
  activity_type?: string;
}): Promise<ActivityItem[]> {
  const ps = new URLSearchParams();
  if (params?.status) ps.set("status", params.status);
  if (params?.keyword) ps.set("keyword", params.keyword);
  if (params?.activity_type) ps.set("activity_type", params.activity_type);
  const qs = ps.toString();
  return request(`/api/admin/activities${qs ? `?${qs}` : ""}`);
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

// T45（FEAT-082）：详情/编辑/封面上传
export function apiGetActivityDetail(id: number): Promise<ActivityItem & {
  enrolled_count: number; pending_count: number; checked_in_count: number;
}> {
  return request(`/api/admin/activities/${id}`);
}

export function apiUpdateActivity(
  id: number,
  body: Partial<{
    title: string; start_at: string; location: string; max_quota: number;
    fee: number; description: string; member_only: boolean; enroll_deadline?: string;
  }>,
): Promise<{ id: number; title: string; status: string }> {
  return request(`/api/admin/activities/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

export function apiUploadActivityCover(id: number, file: File): Promise<{ cover_path: string }> {
  const fd = new FormData();
  fd.append("file", file);
  return request(`/api/admin/activities/${id}/cover`, { method: "POST", body: fd });
}

export function activityCoverUrl(id: number): string {
  // R1（插修 16）：<img> 不带 Authorization——拼 query token（与 request.ts
  // TOKEN_KEY 同源 getToken）；cover-media 双通道已支持
  const token = getToken();
  return `/api/admin/activities/${id}/cover-media${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}
