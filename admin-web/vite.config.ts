import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 局域网可访问（同热点设备如台式机可验收）；生产部署走反代，见 WM12 安全清单
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
    },
  },
  // 代码分割（2026-10-11 A3）：单 chunk 1,558.59 kB（gzip 490 kB）→ 拆出"版本不变"的
  // 依赖（react 系 / antd 系），业务代码改动不再让用户重下整个 vendor。
  // 根因登记：`pages/AuditLog.tsx` 对 `api/admin` 是**动态**导入、其余页面是静态导入 →
  // 该模块无法被 Rollup 拆出（既在入口又在异步块），故此前整包单 chunk（审查 §五 登记项）。
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          antd: ["antd", "@ant-design/icons"],
        },
      },
    },
  },
  // 生产产物本地复验（2026-10-08 上线骨架批）：`pnpm build && pnpm preview` 起 dist 静态服务，
  // /api 同源代理到后端——与 nginx 的「静态 SPA + /api 反代」同构，用来验证构建产物真能跑，
  // 而不只是"构建没报错"（CI 只跑 tsc）。端口固定 4173，避免与 dev 的 5173 混淆。
  preview: {
    port: 4173,
    proxy: {
      "/api": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
    },
  },
});
