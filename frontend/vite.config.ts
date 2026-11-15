import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发期 /api 代理到本机 Flask（127.0.0.1:8001）；仅本机，不对外。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8001", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1600,
  },
});
