// catalog 域 API（类型由 openapi-typescript 生成）
import { request } from "./client";
import type { components } from "./schema";

export type Book = components["schemas"]["BookResponse"];
export type BookCopy = components["schemas"]["CopyResponse"];
export type QuizQuestion = components["schemas"]["QuizQuestionResponse"];
type BookCreate = components["schemas"]["BookCreateRequest"];
type BookUpdate = components["schemas"]["BookUpdateRequest"];
type PaginatedBooks = components["schemas"]["PaginatedResponse_BookResponse_"];

export function apiListBooks(params: {
  page: number;
  page_size: number;
  keyword?: string;
  ar_pending?: boolean;
  status?: number;
}): Promise<PaginatedBooks> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size),
  });
  if (params.keyword) query.set("keyword", params.keyword);
  if (params.ar_pending) query.set("ar_pending", "true");
  if (params.status !== undefined) query.set("status", String(params.status));
  return request(`/api/admin/books?${query.toString()}`);
}

export function apiCreateBook(body: BookCreate): Promise<Book> {
  return request("/api/admin/books", { method: "POST", body: JSON.stringify(body) });
}

export function apiGetBook(id: number): Promise<Book> {
  return request(`/api/admin/books/${id}`);
}

export function apiUpdateBook(id: number, body: BookUpdate): Promise<Book> {
  return request(`/api/admin/books/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

export function apiToggleBookStatus(id: number): Promise<Book> {
  return request(`/api/admin/books/${id}/toggle-status`, { method: "POST" });
}

export function apiListCopies(bookId: number): Promise<BookCopy[]> {
  return request(`/api/admin/books/${bookId}/copies`);
}

export function apiAddCopies(bookId: number, count: number): Promise<BookCopy[]> {
  return request(`/api/admin/books/${bookId}/copies?count=${count}`, { method: "POST" });
}

export function apiUpdateCopyStatus(
  copyId: number,
  body: { status: string; reason: string }
): Promise<BookCopy> {
  return request(`/api/admin/copies/${copyId}/status`, { method: "PUT", body: JSON.stringify(body) });
}

export async function apiUploadCover(bookId: number, file: File): Promise<Book> {
  const form = new FormData();
  form.append("file", file);
  const token = localStorage.getItem("dmkwords_admin_token");
  const res = await fetch(`/api/admin/books/${bookId}/cover`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "上传失败");
  return res.json() as Promise<Book>;
}

export async function apiUploadAudio(bookId: number, file: File): Promise<Book> {
  const form = new FormData();
  form.append("file", file);
  const token = localStorage.getItem("dmkwords_admin_token");
  const res = await fetch(`/api/admin/books/${bookId}/audio`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "上传失败");
  return res.json() as Promise<Book>;
}

export async function apiImportBooks(file: File): Promise<{
  total_rows: number;
  success_count: number;
  failed_count: number;
  errors: string[];
}> {
  const form = new FormData();
  form.append("file", file);
  const token = localStorage.getItem("dmkwords_admin_token");
  const res = await fetch("/api/admin/books/import", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "导入失败");
  return res.json();
}

export function apiListQuestions(bookId: number): Promise<QuizQuestion[]> {
  return request(`/api/admin/books/${bookId}/questions`);
}

export function apiCreateQuestion(
  bookId: number,
  body: {
    question_type: string;
    question_text: string;
    options: string[];
    answer: string;
    sort_order: number;
  }
): Promise<QuizQuestion> {
  return request(`/api/admin/books/${bookId}/questions`, { method: "POST", body: JSON.stringify(body) });
}

export function apiToggleQuestion(id: number): Promise<QuizQuestion> {
  return request(`/api/admin/questions/${id}/toggle-active`, { method: "POST" });
}

export function apiDeleteQuestion(id: number): Promise<{ detail: string }> {
  return request(`/api/admin/questions/${id}`, { method: "DELETE" });
}
