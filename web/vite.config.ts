import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const appName = env.VITE_APP_NAME || "[APP_NAME]";

  return {
    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        registerType: "autoUpdate",
        manifest: {
          name: appName,
          short_name: appName,
          lang: "id",
          display: "standalone",
          background_color: "#ffffff",
          theme_color: "#0f172a",
          icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
        },
        workbox: {
          // App shell di-cache agar aplikasi tetap terbuka saat offline (PRD §6.6).
          navigateFallbackDenylist: [/^\/api\//],
        },
      }),
    ],
    server: {
      proxy: { "/api": "http://localhost:8000" },
    },
  };
});
