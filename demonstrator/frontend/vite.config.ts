import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    strictPort: true,
    proxy: {
      // Preserve Host so the API's same-origin POST check also works through Vite.
      "/api": {
        target: `http://127.0.0.1:${process.env.DEMO_API_PORT || "8765"}`,
        changeOrigin: false,
      },
    },
  },
});
