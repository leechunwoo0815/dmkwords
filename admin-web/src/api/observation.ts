// observation API（WM10：评估报告上传）
import { request } from "./client";

export function apiUploadObservation(
  childId: number, form: FormData,
): Promise<{ id: number; images: string[] }> {
  return request(`/api/admin/children/${childId}/observation-reports`, {
    method: "POST", body: form,
  });
}
