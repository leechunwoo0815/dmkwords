import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { loadPaintFonts } from "./fonts";
import { applyTheme, getTheme, resolveThemeId } from "./index";
import type { ThemeTokens } from "./types";

/**
 * 主题运行时（第一百六十四批）
 *
 * 为什么需要它：`applyTheme()` 只改 CSS 变量，**antd 的 token 得靠 ConfigProvider 重渲染**。
 * 所以把「当前主题 id」放进 React 状态：切换时先同步写 CSS 变量（避免一帧闪烁），
 * 再 `setState` 让 ConfigProvider 换 token —— 两套皮肤一次到位。
 */
interface ThemeContextValue {
  themeId: string;
  theme: ThemeTokens;
  setThemeId: (id: string) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeId, setId] = useState<string>(() => resolveThemeId());
  // 绘本风才下载手绘字体（企业风零字体请求）；刷新后停留在绘本风也会在挂载时加载
  useEffect(() => {
    if (themeId === "paint") loadPaintFonts();
  }, [themeId]);
  const value = useMemo<ThemeContextValue>(
    () => ({
      themeId,
      theme: getTheme(themeId),
      setThemeId: (next: string) => {
        // 先写变量（同步生效，视觉不掉帧），再触发重渲染换 antd token
        applyTheme(next);
        setId(next);
      },
    }),
    [themeId],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useAdminTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useAdminTheme 必须在 ThemeProvider 内使用");
  return ctx;
}
