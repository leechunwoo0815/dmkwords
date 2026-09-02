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

  if (res.status === 401) {
    clearToken();
    window.location.href = "/login";
    throw new ApiError(401, "登录已过期，请重新登录");
  }

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
