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
