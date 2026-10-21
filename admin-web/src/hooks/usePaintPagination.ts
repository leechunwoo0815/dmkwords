import { useCallback, useState } from "react";

const STORAGE_KEY = "admin-web-page-size";
const MAX_PAGE_SIZE = 200;
const MIN_PAGE_SIZE = 1;

/**
 * 读本地保存的每页条数并**按本页上限夹取**。
 *
 * P2-56（2026-10-20 第八轮）：page-size 存在**全局** key 里（`admin-web-page-size`），
 * 在"上限 200"的通知中心填了 200 之后，切到"上限 100"的会员/图书/审计页会带 200 请求
 * → **首屏 422**（页面直接报错）。夹取后跨页污染不再产生 422。
 */
function readStoredPageSize(defaultPageSize: number, maxPageSize: number): number {
  const raw = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
  const n = raw ? Number(raw) : NaN;
  if (!Number.isFinite(n) || n < MIN_PAGE_SIZE) {
    return Math.min(defaultPageSize, maxPageSize);
  }
  return Math.min(n, maxPageSize);
}

export interface PaintPaginationState {
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  onChange: (page: number, pageSize: number) => void;
}

export function usePaintPagination(
  defaultPageSize: number = 15,
  initialPage: number = 1,
  /**
   * 本页接口的每页上限（默认 100 = 多数管理端接口的 `le=100`）。
   * 只有后端确实允许 200 的接口（如通知中心）才传 200——传大了首屏就是 422（P2-56）。
   */
  maxPageSize: number = 100,
): PaintPaginationState {
  const [pageSize, setPageSizeState] = useState(() => readStoredPageSize(defaultPageSize, maxPageSize));
  const [page, setPage] = useState(() => (Number.isFinite(initialPage) && initialPage >= 1 ? Math.floor(initialPage) : 1));

  const setPageSize = useCallback((size: number) => {
    const valid = Math.max(MIN_PAGE_SIZE, Math.min(MAX_PAGE_SIZE, maxPageSize, size));
    setPageSizeState(valid);
    localStorage.setItem(STORAGE_KEY, String(valid));
    setPage(1);
  }, [maxPageSize]);

  const onChange = useCallback(
    (newPage: number, newPageSize: number) => {
      if (newPageSize !== pageSize) {
        setPageSize(newPageSize);
      } else {
        setPage(newPage);
      }
    },
    [pageSize, setPageSize]
  );

  return { page, pageSize, setPage, setPageSize, onChange };
}
