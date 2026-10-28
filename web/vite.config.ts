import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// In development the dev server forwards /api to the FastAPI backend and adds
// the shared API key, as nginx does in Docker, so the key never reaches the browser.
export default defineConfig(({ mode }) => {
  const env: Record<string, string | undefined> = { ...process.env, ...loadEnv(mode, "..", "") };
  const target = env.EKALAVYA_API_URL || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          headers: env.APP_API_KEY ? { "X-API-Key": env.APP_API_KEY } : undefined,
        },
      },
    },
    build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
  };
});
