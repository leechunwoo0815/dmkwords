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
  linkage?: boolean; // T20d：联动单灰态（退款/转让驱动，随退款自动推进，无操作按钮）
}

export function apiListAdminInbox(params: {
  page: number;
  page_size: number;
  status_filter?: string;
  scene?: string;
  keyword?: string;
}): Promise<{ items: AdminInboxItem[]; total: number; pending_count: number; read_only?: boolean; page: number; page_size: number }> {
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

export interface TodoCounts {
  refund_pending: number;
  withdrawal_pending: number;
  transfer_pending: number;
  transfer_expiring: number;
  activity_batch_refund: number;
  order_pending_manual: number;
  admin_total: number;
  /** T1：全体家长通知未读数（胶囊兜底全局源，与视角无关） */
  parent_unread: number;
  /** T6：活动报名待确认（单独口径不进 admin_total，侧边栏活动徽标用） */
  activity_enroll_pending: number;
  /** WM14-A：阅读圈今日新帖未馆长赞数（侧边栏徽标+页顶胶囊共用） */
  circle_unliked: number;
  /** R-313：未入会临时借书产生的入会跟进待办（孩子入会后自动归零） */
  member_follow_up: number;
}

export function apiTodoCounts(): Promise<TodoCounts> {
  return request("/api/admin/todo-counts");
}

export interface TaskSpecItem {
  name: string;
  display_name: string;
  group: string;
  interval_seconds: number;
  /** cron 任务的钟点文案（例：每天 08:00）；interval 任务由前端按秒数折算 */
  schedule_text?: string | null;
  cron_expr?: string | null;
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

// ---------- 媒体体检（docs/15 §二十二） ----------

export type MediaHealth = components["schemas"]["MediaHealthResponse"];
type MediaTrashRequest = components["schemas"]["MediaTrashRequest"];
export type MediaTrashResult = components["schemas"]["MediaTrashResponse"];
export type MediaCensus = components["schemas"]["MediaCensusResponse"];

/** 最新盘点报告 + 回收站统计（专员可看，只读）。 */
export function apiMediaHealth(): Promise<MediaHealth> {
  return request("/api/admin/media/health");
}

/** 把孤儿图移入回收站（**仅超管**；allOrphans=true 表示清当前全部孤儿）。 */
export function apiMediaTrash(
  reason: string,
  allOrphans = true,
  paths: string[] = []
): Promise<MediaTrashResult> {
  const body: MediaTrashRequest = { reason, all_orphans: allOrphans, paths };
  return request("/api/admin/media/trash", { method: "POST", body: JSON.stringify(body) });
}

/** 从回收站还原（**仅超管**）。 */
export function apiMediaRestore(entryId: number): Promise<components["schemas"]["MediaRestoreResponse"]> {
  return request(`/api/admin/media/trash/${entryId}/restore`, { method: "POST" });
}

/** 清空回收站（**仅超管**；永久删除，但删除前仍复检引用，被引用的条目会自动还原）。 */
export function apiMediaEmptyTrash(
  reason: string
): Promise<components["schemas"]["MediaEmptyTrashResponse"]> {
  return request("/api/admin/media/trash/empty", {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export type DashboardCharts = components["schemas"]["DashboardChartsResponse"];

/** 仪表盘图形区数据（2026-09-21）：近 N 天借还趋势 / 热门书 TOP / 会员构成。 */
export function apiDashboardCharts(days = 14, top = 5): Promise<DashboardCharts> {
  return request(`/api/admin/dashboard/charts?days=${days}&top=${top}`);
}

export async function downloadExcel(path: string, filename: string): Promise<void> {
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
