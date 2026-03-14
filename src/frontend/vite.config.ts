import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react-swc";

const hasModuleSideEffects = (id: string, external: boolean) => {
  if (external) return true;
  return (
    id.endsWith(".css") ||
    id.includes("/src/main.tsx") ||
    id.includes("/src/styles/")
  );
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Prevent duplicate package instances in the client bundle.
    dedupe: [
      "react",
      "react-dom",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
      "react-router",
      "shiki",
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
      "shiki",
      "streamdown",
      "@streamdown/cjk",
      "@streamdown/code",
      "@streamdown/math",
      "@streamdown/mermaid",
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
    target: "esnext",
    minify: "oxc",
    cssMinify: "lightningcss",
    rolldownOptions: {
      treeshake: {
        moduleSideEffects: hasModuleSideEffects,
      },
      output: {
        codeSplitting: {
          groups: [
            {
              name: "vendor-ui",
              test: /[/\\]node_modules[/\\](?:@radix-ui|lucide-react|motion|cmdk|embla-carousel-react|next-themes|react-resizable-panels|sonner|vaul)[/\\]/,
            },
            {
              name: "vendor-editor",
              test: /[/\\]node_modules[/\\](?:@codemirror|@lezer|codemirror|crelt|style-mod|w3c-keyname|@marijn\/find-cluster-break)[/\\]/,
            },
            {
              name: "vendor-state",
              test: /[/\\]node_modules[/\\](?:zustand|@tanstack\/react-query|react-router|nanoid|zod)[/\\]/,
            },
            {
              name: "vendor-auth",
              test: /[/\\]node_modules[/\\](?:@azure\/msal-browser|@azure\/msal-common)[/\\]/,
            },
            {
              name: "vendor-analytics",
              test: /[/\\]node_modules[/\\](?:posthog-js|@posthog)[/\\]/,
            },
            {
              name: "vendor-flow",
              test: /[/\\]node_modules[/\\](?:@xyflow\/react|@dagrejs\/dagre)[/\\]/,
            },
            {
              name: "vendor-cytoscape",
              test: /[/\\]node_modules[/\\](?:cytoscape|cytoscape-cose-bilkent)[/\\]/,
            },
            {
              name: "vendor-rive",
              test: /[/\\]node_modules[/\\](?:@rive-app)[/\\]/,
            },
            {
              name: "vendor-ai",
              test: /[/\\]node_modules[/\\](?:ai|@ai-sdk|agentation)[/\\]/,
            },
          ],
        },
      },
    },
  },
});
