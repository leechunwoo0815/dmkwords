// observation API（WM10：评估报告上传 + 管理端查看）
import { getToken, request } from "./client";

export interface ObservationReportRow {
  id: number;
  child_id: number;
  remark: string | null;
  images: string[];
  created_at: string;
}

export function apiUploadObservation(
  childId: number, form: FormData,
): Promise<{ id: number; images: string[] }> {
  return request(`/api/admin/children/${childId}/observation-reports`, {
    method: "POST", body: form,
  });
}

/** 管理端查看已上传报告（2026-11-03：此前只有上传，后台看不到历史）。 */
export function apiListObservation(childId: number): Promise<ObservationReportRow[]> {
  return request(`/api/admin/children/${childId}/observation-reports`);
}

/** 报告图 URL（`<img>` 带不了 Authorization → query token；后端按孩子报告行校验归属）。 */
export function apiObservationImageUrl(childId: number, relPath: string): string {
  const token = getToken();
  const q = new URLSearchParams();
  if (token) q.set("token", token);
  const qs = q.toString();
  const sub = relPath.replace(/^observation\//, "");
  return `/api/admin/children/${childId}/observation-images/${sub}${qs ? `?${qs}` : ""}`;
}
