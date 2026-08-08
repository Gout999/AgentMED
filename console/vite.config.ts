import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// console 构建产物交给 nginx 静态托管；数据通路只走相对路径 /api/*（由 nginx 反代到 control-plane:8090）。
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 5173,
    // 本地开发时直接反代到宿主 control-plane（与 compose 内 nginx 行为一致）
    proxy: {
      "/api": {
        target: "http://127.0.0.1:18090",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
