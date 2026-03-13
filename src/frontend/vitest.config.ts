import { defineConfig } from "vitest/config";

/**
 * Vitest configuration — decoupled from vite.config.ts so the build
 * pipeline never loads test-only dependencies (jsdom, etc.).
 *
 * Run modes
 *   bun run test:unit        – fast single-pass
 *   bun run test:watch       – interactive watch
 *   bun run test:coverage    – v8 coverage report
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": `${import.meta.dirname}/src`,
    },
  },

  test: {
    // ── Environment ─────────────────────────────────────────────────
    // jsdom provides browser globals (localStorage, sessionStorage, fetch …)
    // required by the API client and hooks under test.
    environment: "jsdom",

    // ── Bootstrap ───────────────────────────────────────────────────
    // Loaded before every test file. Use for global mocks / polyfills.
    setupFiles: ["./src/test/setup.ts"],

    // ── File discovery ──────────────────────────────────────────────
    // Unit tests live inside src/**/__tests__/ or are named *.test.*
    // E2E Playwright tests live in tests/ and are excluded here.
    include: [
      "src/**/__tests__/**/*.{test,spec}.{ts,tsx}",
      "src/**/*.{test,spec}.{ts,tsx}",
    ],
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
});
