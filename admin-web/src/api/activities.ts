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

export interface ActivityDetail extends ActivityItem {
  enrolled_count: number;   // 已缴费待参加（status=enrolled）
  pending_count: number;    // 待收款（status=pending_payment）
  checked_in_count: number; // 已签到
  quota_used: number;       // 占位总数（含待收款，后端 ACTIVE_STATUSES 口径）
  quota_left: number;
  full: boolean;
  created_at?: string;
  detail_blocks?: DetailBlock[]; // 图文详情（编辑器可写形态：image 块只有相对路径）
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

export interface SigninResult {
  enrollment_id: number;
  child_id: number;
  // PRD §9.2.1 门店连扫：回执要能当场确认"签的是谁、哪场活动"
  child_name: string | null;
  activity_title: string;
  ticket_code: string;
  checked_in_at: string;
}

export function apiSignin(ticketCode: string): Promise<SigninResult> {
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
export function apiGetActivityDetail(id: number): Promise<ActivityDetail> {
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

// ---------- 图文详情（2026-09-20 客户需求「像公众号一样」）----------

export interface DetailBlock {
  type: "paragraph" | "image";
  text?: string | null;
  path?: string | null;
  caption?: string | null;
}

export interface DetailBlockView {
  type: "paragraph" | "image";
  text?: string;
  image_url?: string;
  caption?: string;
}

/** 图文详情全量覆盖写（顺序即展示顺序）。独立端点：活动开始后仍可编辑（纯展示字段）。 */
export function apiSaveDetailBlocks(
  id: number,
  blocks: DetailBlock[],
): Promise<{ id: number; blocks: DetailBlockView[] }> {
  return request(`/api/admin/activities/${id}/detail-blocks`, {
    method: "PUT",
    body: JSON.stringify({ blocks }),
  });
}

/** 上传一张图文配图，返回相对路径（把它塞进 image 块）。 */
export function apiUploadDetailImage(id: number, file: File): Promise<{ path: string; url: string }> {
  const fd = new FormData();
  fd.append("file", file);
  return request(`/api/admin/activities/${id}/detail-images`, { method: "POST", body: fd });
}

/** 管理端预览配图（<img> 不带 Authorization → query token；走**管理端**端点，
 *  因为 miniapp 那个要家长 token——管理端只有管理员 token）。 */
export function activityDetailImageUrl(id: number, name: string): string {
  const token = getToken();
  const base = `/api/admin/activities/${id}/detail-image?name=${encodeURIComponent(name)}`;
  return token ? `${base}&token=${encodeURIComponent(token)}` : base;
}

/** 从相对路径取文件名（端点只接受 basename）。 */
export function detailImageName(path: string): string {
  return path.split("/").pop() || path;
}
