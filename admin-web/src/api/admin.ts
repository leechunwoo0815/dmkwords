// 管理端 API（类型由 openapi-typescript 从后端 OpenAPI 生成，禁止手写 — 宪法五-3）
import { request } from "./client";
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
