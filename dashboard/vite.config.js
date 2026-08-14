import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],

  // Relative asset paths, so the same build works whether FastAPI mounts it at
  // /app, at the root, or anywhere else.
  base: "./",

  server: {
    port: 5173,
    // In dev the page is served from :5173 but the backend lives on :8000.
    // Proxying keeps every request same-origin, so the browser never applies
    // CORS to /latest and the dev server needs no backend config to work.
    proxy: {
      "/latest": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/calibrate": "http://localhost:8000",
      "/cal_status": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },

  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
