// circle API（WM14-A：阅读圈帖子管理/馆长赞/置顶/删除/概览）
import { getToken, request } from "./client";

export interface CirclePostItem {
  id: number;
  parent_name: string;
  child_name: string;
  child_cn_name?: string;
  card_type: string;
  card_type_label: string;
  image_url: string;
  like_count: number;
  admin_liked: boolean;
  is_pinned: boolean;
  created_at: string;
}

export interface CircleListResp {
  items: CirclePostItem[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
}

export interface CircleOverview {
  week_new_posts: number;
  sharing_parents: number;
  total_likes: number;
  admin_liked_coverage: number;
  card_type_distribution: Record<string, number>;
}

export function apiListCirclePosts(params?: {
  page?: number;
  page_size?: number;
  card_type?: string;
  start?: string;
  end?: string;
  keyword?: string;
}): Promise<CircleListResp> {
  const ps = new URLSearchParams();
  if (params?.page) ps.set("page", String(params.page));
  if (params?.page_size) ps.set("page_size", String(params.page_size));
  if (params?.card_type) ps.set("card_type", params.card_type);
  if (params?.start) ps.set("start", params.start);
  if (params?.end) ps.set("end", params.end);
  if (params?.keyword) ps.set("keyword", params.keyword);
  const qs = ps.toString();
  return request(`/api/admin/circle/posts${qs ? `?${qs}` : ""}`);
}

export function apiCircleCardTypes(): Promise<{
  items: { value: string; label: string }[];
}> {
  return request("/api/admin/circle/card-types");
}

export function apiCircleUnlikedCount(): Promise<{ count: number }> {
  return request("/api/admin/circle/unliked-count");
}

export function apiCircleOverview(): Promise<CircleOverview> {
  return request("/api/admin/circle/overview");
}

export function apiCircleAdminLike(
  id: number,
): Promise<{ post_id: number; admin_liked: boolean; like_count: number }> {
  return request(`/api/admin/circle/posts/${id}/admin-like`, { method: "POST" });
}

export function apiCircleAdminUnlike(
  id: number,
): Promise<{ post_id: number; admin_liked: boolean; like_count: number }> {
  return request(`/api/admin/circle/posts/${id}/admin-like`, { method: "DELETE" });
}

export function apiCirclePin(id: number): Promise<{ post_id: number; is_pinned: boolean }> {
  return request(`/api/admin/circle/posts/${id}/pin`, { method: "POST" });
}

export function apiCircleUnpin(id: number): Promise<{ post_id: number; is_pinned: boolean }> {
  return request(`/api/admin/circle/posts/${id}/pin`, { method: "DELETE" });
}

export function apiCircleDeletePost(id: number, reason: string): Promise<{ id: number; deleted: boolean }> {
  return request(`/api/admin/circle/posts/${id}`, {
    method: "DELETE",
    body: JSON.stringify({ reason }),
  });
}

export function circlePostImageUrl(id: number): string {
  // <img> 不带 Authorization——拼 query token（活动封面 cover-media 先例同款）
  const token = getToken();
  return `/api/admin/circle/posts/${id}/image${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}
