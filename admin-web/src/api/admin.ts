// 管理端 API（类型由 openapi-typescript 从后端 OpenAPI 生成，禁止手写 — 宪法五-3）
import { ApiError, getToken, request } from "./client";
import type { components } from "./schema";

export type AdminUser = components["schemas"]["AdminUserResponse"];
export type SystemConfig = components["schemas"]["SystemConfigResponse"];
export type AuditLog = components["schemas"]["AuditLogResponse"];
type LoginRequest = components["schemas"]["backend__domain__admin__schemas__LoginRequest"];
type SystemConfigUpdateRequest = components["schemas"]["SystemConfigUpdateRequest"];
type PaginatedAuditLogs = components["schemas"]["PaginatedResponse_AuditLogResponse_"];

export function apiLogin(body: LoginRequest): Promise<{ token: string; user: AdminUser }> {
  return request("/api/admin/login", { method: "POST", body: JSON.stringify(body) });
}

export function apiMe(): Promise<{ user: AdminUser; permissions: string[] }> {
  return request("/api/admin/me");
}

export function apiListConfigs(): Promise<SystemConfig[]> {
  return request("/api/admin/configs");
}

export function apiUpdateConfig(
  key: string,
  body: SystemConfigUpdateRequest
): Promise<SystemConfig> {
  return request(`/api/admin/configs/${key}`, { method: "PUT", body: JSON.stringify(body) });
}

type StaffCreateRequest = components["schemas"]["StaffCreateRequest"];
type StaffStatusRequest = components["schemas"]["StaffStatusRequest"];
type StaffResetPasswordRequest = components["schemas"]["StaffResetPasswordRequest"];

export function apiListStaff(): Promise<AdminUser[]> {
  return request("/api/admin/staff");
}

export function apiCreateStaff(body: StaffCreateRequest): Promise<AdminUser> {
  return request("/api/admin/staff", { method: "POST", body: JSON.stringify(body) });
}

export function apiUpdateStaff(
  id: number,
  body: { display_name?: string; role?: string },
): Promise<AdminUser> {
  return request(`/api/admin/staff/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

export function apiSetStaffStatus(id: number, status: number): Promise<AdminUser> {
  return request(`/api/admin/staff/${id}/status`, {
    method: "PUT",
    body: JSON.stringify({ status } satisfies StaffStatusRequest),
  });
}

export function apiResetStaffPassword(id: number, new_password: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/staff/${id}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ new_password } satisfies StaffResetPasswordRequest),
  });
}

export function apiListAuditLogs(params: {
  page: number;
  page_size: number;
  action?: string;
}): Promise<PaginatedAuditLogs> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size),
  });
  if (params.action) query.set("action", params.action);
  return request(`/api/admin/audit-logs?${query.toString()}`);
}

export function apiDashboardOverview(): Promise<components["schemas"]["DashboardOverviewResponse"]> {
  return request("/api/admin/dashboard");
}

// ---------- WM11 通知中心 / 定时任务看板 / 导出 ----------

export interface AdminNotification {
  id: number;
  parent_name: string;
  parent_id: number;
  child_id: number | null;
  category: string;
  scene: string;
  title: string;
  content: string;
  ref_type: string;
  ref_id: string;
  wechat_status: string;
  wechat_error: string;
  read: boolean;
  created_at: string;
}

export function apiListNotifications(params: {
  page: number;
  page_size: number;
  category?: string;
  scene?: string;
  parent_name?: string;
  unread?: boolean;
  read?: boolean;
}): Promise<{ items: AdminNotification[]; total: number; unread: number; all_count: number }> {
  const query = new URLSearchParams({ page: String(params.page), page_size: String(params.page_size) });
  if (params.category) query.set("category", params.category);
  if (params.scene) query.set("scene", params.scene);
  if (params.parent_name) query.set("parent_name", params.parent_name);
  if (params.unread) query.set("unread", "true");
  if (params.read) query.set("read", "true");
  return request(`/api/admin/notifications?${query.toString()}`);
}

export function apiToggleNotificationRead(
  id: number,
  read: boolean,
  reason = ""
): Promise<{ id: number; read: boolean; unread_count: number; total: number }> {
  return request(`/api/admin/notifications/${id}/read-status`, {
    method: "POST",
    body: JSON.stringify({ read, reason }),
  });
}

// ---------- WM13 管理待办收件箱 ----------

export interface AdminInboxItem {
  id: number;
  scene: string;
  title: string;
  content: string;
  ref_type: string;
  ref_id: string;
  applicant_name: string;
  amount: string | null;
  created_at: string;
  handled_at: string | null;
  handled_by_name: string | null;
  effective_status: "pending" | "done" | "invalid";
  status_text: string;
}

export function apiListAdminInbox(params: {
  page: number;
  page_size: number;
  status_filter?: string;
  scene?: string;
  keyword?: string;
}): Promise<{ items: AdminInboxItem[]; total: number; pending_count: number; page: number; page_size: number }> {
  const query = new URLSearchParams({ page: String(params.page), page_size: String(params.page_size) });
  if (params.status_filter) query.set("status_filter", params.status_filter);
  if (params.scene) query.set("scene", params.scene);
  if (params.keyword) query.set("keyword", params.keyword);
  return request(`/api/admin/admin-notifications?${query.toString()}`);
}

export function apiHandleAdminInbox(
  id: number,
  reason: string
): Promise<{ id: number; handled: boolean; already: boolean }> {
  return request(`/api/admin/admin-notifications/${id}/handle`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export interface TaskSpecItem {
  name: string;
  display_name: string;
  group: string;
  interval_seconds: number;
  last_run?: { status: string; processed: number; error: string | null; started_at: string } | null;
}

export interface TaskRunItem {
  task_name: string;
  status: string;
  processed: number;
  error: string | null;
  started_at: string;
  finished_at: string;
}

export function apiTaskSpecs(): Promise<{ items: TaskSpecItem[] }> {
  return request("/api/admin/tasks");
}

export function apiTaskRuns(limit = 20): Promise<{ items: TaskRunItem[] }> {
  return request(`/api/admin/tasks/runs?limit=${limit}`);
}

export function apiRunTask(taskName: string): Promise<{ task: string; status: string; processed?: number; error?: string }> {
  return request(`/api/admin/tasks/${taskName}/run`, { method: "POST" });
}

async function downloadExcel(path: string, filename: string): Promise<void> {
  const token = getToken();
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new ApiError(res.status, ((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "导出失败");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function apiExportAuditLogs(): Promise<void> {
  return downloadExcel("/api/admin/audit-logs/export", "audit-logs.xlsx");
}

export function apiExportDashboard(): Promise<void> {
  return downloadExcel("/api/admin/dashboard/export", "dashboard.xlsx");
}

export function apiExportNotifications(): Promise<void> {
  return downloadExcel("/api/admin/notifications/export", "notifications.xlsx");
}
