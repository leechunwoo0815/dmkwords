// API 客户端：统一 fetch 封装（Bearer 注入 / 401 统一登出 / 错误消息透出）
const TOKEN_KEY = "dmkwords_admin_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * W6-D14（2026-10-28）：401 的统一处理（清 token + 回登录页）。
 * 直接 fetch / XHR 的下载、导出、上传通道原先各自只抛文案——token 过期后留在原页
 * 反复点、反复 401。所有绕过 request() 的通道都必须调本函数。
 */
export function handleAuthStatus(status: number): void {
  if (status === 401) {
    clearToken();
    window.location.href = "/login";
    throw new ApiError(401, "登录已过期，请重新登录");
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  // F6：FormData 禁设 Content-Type（覆盖浏览器自动生成的 multipart boundary=解析失败）
  // （E-20260901-03 反模式 3；影响面：apiUploadVoucher/apiUploadObservation 两处
  // 走 request 的 FormData；封面/音频走 catalog.ts uploadWithProgress XHR 独立通道不受影响）
  const isForm = options.body instanceof FormData;
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  handleAuthStatus(res.status);

  if (!res.ok) {
    // F6：FastAPI 422 的 detail 是数组 [{loc,msg,type}]——提取 msg 拼接，
    // 禁 String(数组) = [object Object]（E-20260901-03 反模式 3）
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const msg = Array.isArray(body.detail)
      ? body.detail.map((e) => (e as { msg?: string }).msg ?? JSON.stringify(e)).join("; ")
      : (body.detail as string | undefined);
    throw new ApiError(res.status, msg ?? `请求失败（${res.status}）`);
  }

  return (await res.json()) as T;
}

/** 列表类响应：数据 + **硬上限元信息**（P2-44）。 */
export interface ListResult<T> {
  items: T;
  /** 该接口生效的行数上限（响应头 `X-List-Limit`）；缺失为 null */
  limit: number | null;
  /** 当前筛选下的**总行数**（响应头 `X-Total-Count`，服务端分页接口才有）；缺失为 null */
  total: number | null;
  /** 还有更多未返回（响应头 `X-List-Truncated`）——页面须提示，不能当成"这就是全部" */
  truncated: boolean;
}

/**
 * 列表接口（P2-44）：在 `request` 之上读 `X-List-Limit` / `X-List-Truncated` 两个响应头。
 *
 * 为什么用响应头而不是把响应体包成 `{items,total}`：这些列表接口的消费方遍布管理端与
 * 小程序，改响应体形状是**破坏性变更**（还要同步 OpenAPI 生成的 schema.d.ts）；
 * 响应头对既有消费方零影响。后端见 `backend/common/list_meta.py`。
 */
export async function requestList<T>(
  path: string, options: RequestInit = {},
): Promise<ListResult<T>> {
  const token = getToken();
  const isForm = options.body instanceof FormData;
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  handleAuthStatus(res.status);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const msg = Array.isArray(body.detail)
      ? body.detail.map((e) => (e as { msg?: string }).msg ?? JSON.stringify(e)).join("; ")
      : (body.detail as string | undefined);
    throw new ApiError(res.status, msg ?? `请求失败（${res.status}）`);
  }

  const limitRaw = res.headers.get("X-List-Limit");
  const limit = limitRaw === null ? null : Number(limitRaw);
  const totalRaw = res.headers.get("X-Total-Count");
  const total = totalRaw === null ? null : Number(totalRaw);
  return {
    items: (await res.json()) as T,
    limit: limit !== null && Number.isFinite(limit) ? limit : null,
    total: total !== null && Number.isFinite(total) ? total : null,
    truncated: res.headers.get("X-List-Truncated") === "true",
  };
}
