// hooks/useTodoCounts.ts — WM13 感知层共享数据源（徽标与待办卡一次请求两处消费）
// U10 防假 0：拉取失败保持上次值并置 failed，禁把失败渲染成 0；
// L3 及时刷新：60s 轮询 + wm13-todo-refresh 事件（审核操作完成主动触发）+ 首次挂载立即拉取。
import { useSyncExternalStore } from "react";

import { apiTodoCounts, TodoCounts } from "../api/admin";

export const TODO_REFRESH_EVENT = "wm13-todo-refresh";

let cache: TodoCounts | null = null;
let loadFailed = false;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

async function fetchOnce() {
  try {
    cache = await apiTodoCounts();
    loadFailed = false;
  } catch {
    loadFailed = true; // 保持上次值（不覆盖为空/0）
  }
  notify();
}

function ensureStarted() {
  if (timer) return;
  void fetchOnce();
  timer = setInterval(() => void fetchOnce(), 60000);
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  ensureStarted();
  if (typeof window !== "undefined") {
    window.addEventListener(TODO_REFRESH_EVENT, handleEvent);
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
      if (typeof window !== "undefined") {
        window.removeEventListener(TODO_REFRESH_EVENT, handleEvent);
      }
    }
  };
}

function handleEvent() {
  void fetchOnce();
}

function getSnapshot(): { counts: TodoCounts | null; failed: boolean } {
  return { counts: cache, failed: loadFailed };
}

/** 徽标/待办卡共用：同一缓存、同一轮询（Q10 裁定：事件机制 + 路由变化由调用方配合触发）。 */
export function useTodoCounts() {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { ...snap, reload: fetchOnce };
}
