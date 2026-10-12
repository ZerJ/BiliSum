import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const root = __dirname;
const backendStaticRoot = path.resolve(root, "../web/static");
const accessToken = process.env.VIDEO_SUM_ACCESS_TOKEN || "";
const proxyHeaders = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;
const backendTarget = process.env.BILISUM_DEV_BACKEND_URL || "http://127.0.0.1:3838";

export default defineConfig(({ command }) => ({
  root,
  base: command === "build" ? "/static/" : "/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 3000,
    strictPort: true,
    fs: {
      allow: [root, path.resolve(root, "..")],
    },
    proxy: {
      "/api": {
        target: backendTarget,
        headers: proxyHeaders,
      },
      "/health": backendTarget,
      "/media": {
        target: backendTarget,
        headers: proxyHeaders,
      },
      "/static/assets/icons": backendTarget,
      "/static/favicon.ico": backendTarget,
      "/static/favicon.svg": backendTarget,
      "/static/favicon-32x32.png": backendTarget,
      "/static/apple-touch-icon.png": backendTarget,
    },
  },
  build: {
    outDir: backendStaticRoot,
    emptyOutDir: false,
    assetsDir: "assets",
  },
}));
