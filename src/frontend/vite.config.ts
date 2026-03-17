import { defineConfig } from "vite";
import path from "path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react-swc";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Prevent duplicate React instances — react-router (and other deps)
    // must use the exact same React that the host provides.
    dedupe: [
      "react",
      "react-dom",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
    ],
    alias: {
      // Alias @ to the src directory
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // Local backend proxy for fleet-rlm integration.
  // Env-driven direct URLs remain supported and are preferred for deployed targets.
  server: {
    proxy: {
      "/health": "http://localhost:8000",
      "/ready": "http://localhost:8000",
      "/api/v1": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ["**/*.svg", "**/*.csv"],

  // Force Vite to pre-bundle CodeMirror packages and their transitive deps
  optimizeDeps: {
    holdUntilCrawlEnd: false,
    include: [
      "react",
      "react-dom",
      "react-router",
      "@codemirror/state",
      "@codemirror/view",
      "@codemirror/commands",
      "@codemirror/language",
      "@codemirror/lang-python",
      "@codemirror/autocomplete",
      "@lezer/highlight",
      "@lezer/common",
      "@lezer/python",
      "codemirror",
      "@marijn/find-cluster-break",
      "style-mod",
      "w3c-keyname",
      "crelt",
    ],
  },

  build: {
    minify: "oxc",
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-ui": [
            "@base-ui/react/accordion",
            "@base-ui/react/dialog",
            "@base-ui/react/menu",
            "lucide-react",
            "motion",
          ],
          "vendor-editor": ["codemirror"],
          "vendor-state": ["zustand", "@tanstack/react-query", "react-router"],
        },
      },
    },
  },
});
