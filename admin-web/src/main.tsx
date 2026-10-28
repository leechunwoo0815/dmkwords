import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { paintTheme } from "./theme-paint";
// B8（2026-10-28）：字体自托管（@fontsource）——移除 Google Fonts / jsDelivr 外链，
// 避免外链样式无法做 SRI、且离线/内网环境字体加载失败。
import "@fontsource/zcool-kuaile/chinese-simplified-400.css";
import "@fontsource/zcool-kuaile/latin-400.css";
import "@fontsource/nunito/400.css";
import "@fontsource/nunito/600.css";
import "@fontsource/nunito/700.css";
import "@fontsource/nunito/800.css";
import "./styles/paint.css";
import PaintEmpty from "./components/PaintEmpty";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider theme={paintTheme} locale={zhCN} renderEmpty={() => <PaintEmpty character="default" />}>
      <ErrorBoundary>
      <AntdApp>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AntdApp>
      </ErrorBoundary>
    </ConfigProvider>
  </StrictMode>
);
