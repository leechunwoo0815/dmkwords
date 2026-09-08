import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { paintTheme } from "./theme-paint";
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
