// 管理端 API（类型由 openapi-typescript 从后端 OpenAPI 生成，禁止手写 — 宪法五-3）
import { request } from "./client";
import type { components } from "./schema";

export type AdminUser = components["schemas"]["AdminUserResponse"];
export type SystemConfig = components["schemas"]["SystemConfigResponse"];
export type AuditLog = components["schemas"]["AuditLogResponse"];
type LoginRequest = components["schemas"]["LoginRequest"];
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
