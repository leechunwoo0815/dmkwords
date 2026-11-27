// reservations API（WM6：预约管理 / 核销 / 孩子阅读档案）
import { ListResult, request, requestList } from "./client";
import type { components } from "./schema";

// B5-8：本文件四个类型原先手写，逐一比对后与 OpenAPI 契约**逐字段等价**（字段名/可选性/类型
// 全一致，且这三个端点 200 响应就声明了这些 schema）→ 改为契约类型，导出名保持不变。
// 预约列表端点（GET /api/admin/reservations）200 = reading 路由的 ReservationItemResponse
// （circulation 路由同名 schema 是 miniapp 口径，字段更少，别混用）。
export type ReservationItem = components["schemas"]["backend__domain__reading__router__ReservationItemResponse"];
export type CheckOutResult = components["schemas"]["CheckOutResponse"];
export type FinishedBook = components["schemas"]["FinishedBookItem"];
export type ChildReadingProfile = components["schemas"]["ChildReadingProfileResponse"];

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
