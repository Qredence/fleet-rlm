import { defineConfig } from "vite-plus";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  lint: {
    plugins: ["oxc", "typescript", "unicorn", "react"],
    categories: {
      correctness: "warn",
    },
    env: {
      builtin: true,
    },
    ignorePatterns: ["dist"],
    overrides: [
      {
        files: ["**/*.{ts,tsx}"],
        rules: {
          "constructor-super": "off",
          "for-direction": "error",
          "getter-return": "off",
          "no-async-promise-executor": "error",
          "no-case-declarations": "error",
          "no-class-assign": "off",
          "no-compare-neg-zero": "error",
          "no-cond-assign": "error",
          "no-const-assign": "off",
          "no-constant-binary-expression": "error",
          "no-constant-condition": "error",
          "no-control-regex": "error",
          "no-debugger": "error",
          "no-delete-var": "error",
          "no-dupe-class-members": "off",
          "no-dupe-else-if": "error",
          "no-dupe-keys": "off",
          "no-duplicate-case": "error",
          "no-empty": "error",
          "no-empty-character-class": "error",
          "no-empty-pattern": "error",
          "no-empty-static-block": "error",
          "no-ex-assign": "error",
          "no-extra-boolean-cast": "error",
          "no-fallthrough": "error",
          "no-func-assign": "off",
          "no-global-assign": "error",
          "no-import-assign": "off",
          "no-invalid-regexp": "error",
          "no-irregular-whitespace": "error",
          "no-loss-of-precision": "error",
          "no-misleading-character-class": "error",
          "no-new-native-nonconstructor": "off",
          "no-nonoctal-decimal-escape": "error",
          "no-obj-calls": "off",
          "no-prototype-builtins": "error",
          "no-redeclare": "off",
          "no-regex-spaces": "error",
          "no-self-assign": "error",
          "no-setter-return": "off",
          "no-shadow-restricted-names": "error",
          "no-sparse-arrays": "error",
          "no-this-before-super": "off",
          "no-undef": "off",
          "no-unexpected-multiline": "error",
          "no-unreachable": "off",
          "no-unsafe-finally": "error",
          "no-unsafe-negation": "off",
          "no-unsafe-optional-chaining": "error",
          "no-unused-labels": "error",
          "no-unused-private-class-members": "error",
          "no-unused-vars": [
            "warn",
            {
              argsIgnorePattern: "^_",
              varsIgnorePattern: "^_",
            },
          ],
          "no-useless-backreference": "error",
          "no-useless-catch": "error",
          "no-useless-escape": "error",
          "no-with": "off",
          "require-yield": "error",
          "use-isnan": "error",
          "valid-typeof": "error",
          "no-var": "error",
          "prefer-const": "error",
          "prefer-rest-params": "error",
          "prefer-spread": "error",
          "@typescript-eslint/ban-ts-comment": "error",
          "no-array-constructor": "error",
          "@typescript-eslint/no-duplicate-enum-values": "error",
          "@typescript-eslint/no-empty-object-type": "off",
          "@typescript-eslint/no-explicit-any": "error",
          "@typescript-eslint/no-extra-non-null-assertion": "error",
          "@typescript-eslint/no-misused-new": "error",
          "@typescript-eslint/no-namespace": "error",
          "@typescript-eslint/no-non-null-asserted-optional-chain": "error",
          "@typescript-eslint/no-require-imports": "error",
          "@typescript-eslint/no-this-alias": "error",
          "@typescript-eslint/no-unnecessary-type-constraint": "error",
          "@typescript-eslint/no-unsafe-declaration-merging": "error",
          "@typescript-eslint/no-unsafe-function-type": "error",
          "no-unused-expressions": "error",
          "@typescript-eslint/no-wrapper-object-types": "error",
          "@typescript-eslint/prefer-as-const": "error",
          "@typescript-eslint/prefer-namespace-keyword": "error",
          "@typescript-eslint/triple-slash-reference": "error",
          "react-hooks/rules-of-hooks": "error",
          "react-hooks/exhaustive-deps": "warn",
          "react/only-export-components": "off",
        },
        env: {
          es2020: true,
          browser: true,
        },
      },
      {
        files: ["src/components/ui/**/*.{ts,tsx}", "src/components/ai-elements/**/*.{ts,tsx}", "src/components/patterns/**/*.{ts,tsx}"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: [
                {
                  group: ["@/screens/*"],
                  message: "Shared components must not depend on screen-owned modules.",
                },
              ],
            },
          ],
        },
      },
      {
        files: [
          "src/features/workspace/{use-workspace.ts,workspace-layout-contract.ts}",
          "src/lib/workspace/**/*.{ts,tsx}",
        ],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: [
                {
                  group: [
                    "@/app/workspace/**",
                    "@/features/workspace/workspace-canvas-panel",
                    "@/features/workspace/workspace-screen",
                  ],
                  message:
                    "Workspace runtime/state modules must not depend on workspace UI modules.",
                },
              ],
            },
          ],
        },
      },
      {
        files: ["src/features/layout/**/*.{ts,tsx}"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: [
                {
                  group: [
                    "@/features/workspace/**",
                    "@/screens/volumes/**",
                    "!@/features/workspace/workspace-canvas-panel",
                    "!@/features/workspace/workspace-layout-contract",
                    "!@/screens/volumes/volumes-canvas-panel",
                    "!@/screens/volumes/volumes-layout-contract",
                  ],
                  message:
                    "Layout modules must import screen-owned panels through top-level screen contracts only.",
                },
              ],
            },
          ],
        },
      },
    ],
    options: {},
  },
  plugins: [TanStackRouterVite(), react(), tailwindcss()],
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
      "@": path.resolve(__dirname, "src"),
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
        "src/app/app.tsx",
        "src/features/layout/root-layout.tsx",
        "src/features/workspace/workspace-screen.tsx",
        "src/app/workspace/transcript/workspace-message-list.tsx",
        "src/app/workspace/workspace-composer.tsx",
        "src/screens/settings/settings-screen.tsx",
        "src/screens/settings/runtime-form.tsx",
        "src/screens/volumes/volumes-screen.tsx",
        "src/screens/volumes/volumes-canvas-panel.tsx",
      ],
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ["**/*.svg", "**/*.csv"],

  test: {
    // ── Environment ─────────────────────────────────────────────────
    // jsdom provides browser globals (localStorage, sessionStorage, fetch …)
    // required by the API client and hooks under test.
    environment: "jsdom",
    testTimeout: 15000,

    // ── Bootstrap ───────────────────────────────────────────────────
    // Loaded before every test file. Use for global mocks / polyfills.
    setupFiles: ["./src/test/setup.ts"],

    // ── File discovery ──────────────────────────────────────────────
    // Unit tests live inside src/**/__tests__/ or are named *.test.*
    // E2E Playwright tests live in tests/ and are excluded here.
    include: ["src/**/__tests__/**/*.{test,spec}.{ts,tsx}", "src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", "dist", "tests/e2e/**"],

    // ── Globals ─────────────────────────────────────────────────────
    // Keep false — all vitest APIs are explicitly imported in test files,
    // which is better for IDE support and refactoring safety.
    globals: false,

    // ── Coverage ────────────────────────────────────────────────────
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/main.tsx",
        "src/**/*.d.ts",
        "src/test/**",
        "src/**/__tests__/**",
        "src/lib/rlm-api/generated/**",
        "src/components/ui/**", // shadcn primitives — not domain logic
      ],
      thresholds: {
        lines: 60,
        functions: 60,
        branches: 50,
        statements: 60,
      },
    },
  },
  build: {
    target: "esnext",
    minify: "oxc",
    cssMinify: "lightningcss",
  },
});
