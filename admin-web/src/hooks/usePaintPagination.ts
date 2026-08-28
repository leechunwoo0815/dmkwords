import { useCallback, useState } from "react";

const STORAGE_KEY = "admin-web-page-size";
const MAX_PAGE_SIZE = 200;
const MIN_PAGE_SIZE = 1;

function readStoredPageSize(defaultPageSize: number): number {
  const raw = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
  const n = raw ? Number(raw) : NaN;
  if (!Number.isFinite(n) || n < MIN_PAGE_SIZE || n > MAX_PAGE_SIZE) {
    return defaultPageSize;
  }
  return n;
}

export interface PaintPaginationState {
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  onChange: (page: number, pageSize: number) => void;
}

export function usePaintPagination(defaultPageSize: number = 15, initialPage: number = 1): PaintPaginationState {
  const [pageSize, setPageSizeState] = useState(() => readStoredPageSize(defaultPageSize));
  const [page, setPage] = useState(() => (Number.isFinite(initialPage) && initialPage >= 1 ? Math.floor(initialPage) : 1));

  const setPageSize = useCallback((size: number) => {
    const valid = Math.max(MIN_PAGE_SIZE, Math.min(MAX_PAGE_SIZE, size));
    setPageSizeState(valid);
    localStorage.setItem(STORAGE_KEY, String(valid));
    setPage(1);
  }, []);

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
