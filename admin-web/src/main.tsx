import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

import App from "./App";
import { BRAND } from "./branding";
import ErrorBoundary from "./components/ErrorBoundary";
import PaintEmpty from "./components/PaintEmpty";
import { applyTheme, buildAntdTheme } from "./theme";
import { ThemeProvider, useAdminTheme } from "./theme/ThemeProvider";
// B8（2026-10-28）：字体曾自托管（@fontsource/zcool-kuaile + nunito），为的是去掉 Google Fonts
// 外链。第一百六十四批（2026-11-04）改为**系统字体栈**（Segoe UI + 微软雅黑 / 苹方）：
// 零网络请求、Windows 不再回退失真，且手绘体在企业风后台里不再合适——见 src/theme/types.ts。
import "./styles/paint.css";
// 绘本风皮肤（只在 [data-theme="paint"] 下生效，企业风不受影响）
import "./styles/skins/paint.css";
import "./styles/skins/dark.css";

// 开机注入主题变量：必须早于 render，否则首帧 antd 组件拿不到 CSS 变量（浅色闪烁）。
// ConfigProvider 的 antd token 与这些变量同源（buildAntdTheme 从同一份令牌派生）。
applyTheme();
// index.html 不再写死品牌名；首帧标题也来自 branding.ts（路由页面随后会覆盖）
document.title = BRAND.documentSuffix;

/** ConfigProvider 放在 Provider 内层：切换主题时 antd token 与 CSS 变量一起换。 */
function Root() {
  const { theme } = useAdminTheme();
  return (
    <ConfigProvider
      theme={buildAntdTheme(theme)}
      locale={zhCN}
      renderEmpty={() => <PaintEmpty />}
    >
      <ErrorBoundary>
        <AntdApp>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AntdApp>
      </ErrorBoundary>
    </ConfigProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <Root />
    </ThemeProvider>
  </StrictMode>
);
