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
}): Promise<{
  items: AdminInboxItem[];
  total: number;
  /** P2-2（第七轮）：该筛选下历史精确总数（`total` 是候选集内条数，只用于分页） */
  total_all: number;
  /** 候选集被窗口截断：已审结历史只取最近 N 条（未销项一条不落） */
  truncated: boolean;
  pending_count: number;
  read_only?: boolean;
  page: number;
  page_size: number;
}> {
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
  /** G5（2026-10-14）：预约异常待跟进（副本转维护/遗失且无副本可换；馆员线下跟进后手动销项） */
  reservation_exception: number;
  // P1 #10（2026-10-15 大审查）：后端 `todo_counts` 里**计入 admin_total** 但前端类型漏声明的
  // 5 个键——仪表盘待办卡靠这些键求和渲染，缺声明就只能漏列（"全部处理完毕"假绿把资金异常盖掉）。
  /** 2026-10-12 拍板：活动取消后作废的订单（待人工销项） */
  activity_orders_voided: number;
  /** 2026-10-12 拍板：副本报损·遗失赔偿待处理 */
  copy_damage_pending: number;
  /** 2026-10-12 拍板：资金异常（回调被兜底/迟到支付等，需人工核对） */
  payment_anomaly: number;
  /** G12（2026-10-14）：待评估超过 7 天的孩子跟进名单 */
  pending_evaluation_followup: number;
  /** G1（2026-10-14）：押金不足扣减的「待结清」持续提醒 */
  deposit_settle_followup: number;
  // P2-28（2026-10-17 第六十九批·大审查）：审核超时提醒补齐到三条（此前只有转让）——
  // 退款/退会待审超过配置时限后各写一条待办，**计入 admin_total**（超管视角）。
  /** 退款申请待审超时（`refund_review_timeout_hours`，默认 72 小时） */
  refund_review_timeout: number;
  /** 退会申请待审超时（`withdrawal_review_timeout_days`，默认 3 天） */
  withdrawal_review_timeout: number;
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
  // 2026-10-12 拍板（P10）：连续失败次数（看板标红用；一次成功会把 last_run 洗白）
  consecutive_failures?: number;
  /** P2-21（2026-10-17）：本账号能否手动触发该任务（资金/系统组仅超管；判据同服务层强制点） */
  manual_allowed?: boolean;
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

// ---------- 死信看板（P2-1，2026-10-18 第七十八批） ----------
// 事件处理器重试后仍失败 = 跨域副作用（积分/词数/通知/审计）静默缺失；此前**没有任何读取面**。

export type DeadLetterItem = {
  id: number;
  event_type: string;
  handler_name: string;
  error: string;
  payload: string;
  retry_count: number;
  create_time: string | null;
  handled_at: string | null;
  handled_by: number | null;
};

export type DeadLetterList = {
  items: DeadLetterItem[];
  total: number;
  pending: number;
  page: number;
  page_size: number;
};

export function apiDeadLetters(unresolvedOnly = false): Promise<DeadLetterList> {
  return request(`/api/admin/dead-letters?unresolved_only=${unresolvedOnly}&page_size=20`);
}

export function apiHandleDeadLetter(
  id: number
): Promise<{ id: number; handled: boolean; already_handled: boolean }> {
  return request(`/api/admin/dead-letters/${id}/handle`, { method: "POST" });
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

// ---------- 支付对账（WM12-B，docs/09 WM12-B） ----------

export type PaymentReconcileDiff = components["schemas"]["PaymentReconcileDiff"];
export type PaymentReconcileReport = components["schemas"]["PaymentReconcileReport"];
export type PaymentReconcileRun = components["schemas"]["PaymentReconcileRunResponse"];

/** 最近几轮对账报告（专员可读：账对不对是公开看板）。 */
export function apiPaymentReconciliations(limit = 10): Promise<{ items: PaymentReconcileReport[] }> {
  return request(`/api/admin/payments/reconciliations?limit=${limit}`);
}

/** 手动跑一轮对账（**仅超管**）：本地一致性审计 + 微信账单比对（真通道时）。 */
export function apiRunReconcile(billDate = "", withWechat = true): Promise<PaymentReconcileRun> {
  return request("/api/admin/payments/reconcile", {
    method: "POST",
    body: JSON.stringify({ bill_date: billDate, with_wechat: withWechat }),
  });
}
