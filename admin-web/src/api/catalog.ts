// catalog 域 API（类型由 openapi-typescript 生成）
import { request } from "./client";
import type { components } from "./schema";

export type Book = components["schemas"]["BookResponse"];
export type BookCopy = components["schemas"]["CopyResponse"];
export type QuizQuestion = components["schemas"]["QuizQuestionResponse"];
type BookCreate = components["schemas"]["BookCreateRequest"];
type BookUpdate = components["schemas"]["BookUpdateRequest"];
type PaginatedBooks = components["schemas"]["BookListResponse"];

export function apiListBooks(params: {
  page: number;
  page_size: number;
  keyword?: string;
  ar_pending?: boolean;
  status?: number;
  no_cover?: boolean;
  no_audio?: boolean;
  quiz_incomplete?: boolean;
  sort?: string;
  order?: string;
}): Promise<PaginatedBooks> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size),
  });
  if (params.keyword) query.set("keyword", params.keyword);
  if (params.ar_pending) query.set("ar_pending", "true");
  if (params.status !== undefined) query.set("status", String(params.status));
  if (params.no_cover) query.set("no_cover", "true");
  if (params.no_audio) query.set("no_audio", "true");
  if (params.quiz_incomplete) query.set("quiz_incomplete", "true");
  if (params.sort) query.set("sort", params.sort);
  if (params.order) query.set("order", params.order);
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

export function apiDeleteBook(id: number): Promise<{ detail: string }> {
  return request(`/api/admin/books/${id}`, { method: "DELETE" });
}

export function apiBatchDeleteBooks(ids: number[]): Promise<{ detail: string; success: number; failed: number; errors: string[] }> {
  return request("/api/admin/books/batch-delete", { method: "POST", body: JSON.stringify({ ids }) });
}

export function apiBatchToggleBookStatus(
  ids: number[],
  status: 0 | 1
): Promise<{ detail: string; success: number; failed: number; errors: string[] }> {
  return request("/api/admin/books/batch-toggle-status", {
    method: "POST",
    body: JSON.stringify({ ids, status }),
  });
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

function uploadWithProgress(
  url: string,
  file: File,
  onProgress?: (percent: number) => void
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const token = localStorage.getItem("dmkwords_admin_token");
    xhr.open("POST", url);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          resolve(xhr.responseText);
        }
      } else {
        let detail = "上传失败";
        try {
          detail = JSON.parse(xhr.responseText).detail || detail;
        } catch {
          /* ignore */
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.onabort = () => reject(new Error("上传已取消"));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export function apiUploadCover(
  bookId: number,
  file: File,
  onProgress?: (percent: number) => void
): Promise<Book> {
  return uploadWithProgress(`/api/admin/books/${bookId}/cover`, file, onProgress) as Promise<Book>;
}

export function apiUploadAudio(
  bookId: number,
  file: File,
  onProgress?: (percent: number) => void
): Promise<Book> {
  return uploadWithProgress(`/api/admin/books/${bookId}/audio`, file, onProgress) as Promise<Book>;
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

export async function apiDownloadImportTemplate(): Promise<void> {
  const token = localStorage.getItem("dmkwords_admin_token");
  const res = await fetch("/api/admin/books/import-template", {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))) as { detail?: string }).detail ?? "模板下载失败");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "books-import-template.xlsx";
  a.click();
  URL.revokeObjectURL(url);
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
  }
): Promise<QuizQuestion> {
  return request(`/api/admin/books/${bookId}/questions`, { method: "POST", body: JSON.stringify(body) });
}

export function apiUpdateQuestion(
  questionId: number,
  body: {
    question_type: string;
    question_text: string;
    options: string[];
    answer: string;
  }
): Promise<QuizQuestion> {
  return request(`/api/admin/questions/${questionId}`, { method: "PUT", body: JSON.stringify(body) });
}

export function apiMediaUrl(bookId: number, kind: "cover" | "audio", version?: string): string {
  const token = localStorage.getItem("dmkwords_admin_token");
  const q = new URLSearchParams();
  if (token) q.set("token", token);
  if (version) q.set("v", version);
  const qs = q.toString();
  return `/api/admin/books/${bookId}/${kind}-media${qs ? `?${qs}` : ""}`;
}

export function apiToggleQuestion(id: number): Promise<QuizQuestion> {
  return request(`/api/admin/questions/${id}/toggle-active`, { method: "POST" });
}

export function apiDeleteQuestion(id: number): Promise<{ detail: string }> {
  return request(`/api/admin/questions/${id}`, { method: "DELETE" });
}
