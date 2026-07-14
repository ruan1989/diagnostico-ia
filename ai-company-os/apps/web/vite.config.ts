import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Em produção (GitHub Pages) o site vive em /diagnostico-ia/. Em dev, na raiz.
export default defineConfig(({ mode }) => ({
  base: mode === "production" ? "/diagnostico-ia/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Só usado se você rodar o backend local e definir VITE_API_URL=/api.
      "/api": { target: "http://localhost:4000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
}));
