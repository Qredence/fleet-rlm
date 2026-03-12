import { defineConfig } from "vite";
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
      "@": `${import.meta.dirname}/src`,
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
    warmup: {
      clientFiles: [
        "src/app/App.tsx",
        "src/app/routes.ts",
        "src/app/layout/RootLayout.tsx",
        "src/features/rlm-workspace/RlmWorkspace.tsx",
        "src/features/rlm-workspace/ChatMessageList.tsx",
        "src/components/chat/ChatInput.tsx",
      ],
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ["**/*.svg", "**/*.csv"],

  // Force Vite to pre-bundle CodeMirror packages and their transitive deps
  optimizeDeps: {
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
    cssMinify: "lightningcss",
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "vendor-ui",
              test: /radix-ui|lucide-react|motion/,
            },
            {
              name: "vendor-editor",
              test: /codemirror/,
            },
            {
              name: "vendor-state",
              test: /zustand|tanstack\/react-query|react-router/,
            },
          ],
        },
      },
    },
  },
});
