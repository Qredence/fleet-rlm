import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

// Helper to build an absolute path into node_modules.
// `path.resolve` follows pnpm symlinks at config-load time so the
// resulting string is a real filesystem path Vite can read directly —
// bypassing the `import-analysis` plugin's module resolution which
// fails for scoped packages in the Figma Make pnpm sandbox.
const nm = (...segments: string[]) =>
  path.resolve(__dirname, 'node_modules', ...segments)

export default defineConfig({
  plugins: [
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
  ],
  resolve: {
    // Prevent duplicate React instances — react-router (and other deps)
    // must use the exact same React that the host provides.
    dedupe: ['react', 'react-dom', 'react/jsx-runtime', 'react/jsx-dev-runtime'],
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),

      // ── CodeMirror / Lezer explicit aliases ──────────────────────
      // Vite's `import-analysis` plugin cannot resolve scoped
      // `@codemirror/*` / `@lezer/*` specifiers in this sandbox
      // (no `.vite/deps` cache, and Node resolution fails through
      // pnpm symlinks).  Map every package to its ESM entry point so
      // Vite gets a concrete file path it can process.
      '@codemirror/state':        nm('@codemirror/state/dist/index.js'),
      '@codemirror/view':         nm('@codemirror/view/dist/index.js'),
      '@codemirror/commands':     nm('@codemirror/commands/dist/index.js'),
      '@codemirror/language':     nm('@codemirror/language/dist/index.js'),
      '@codemirror/lang-python':  nm('@codemirror/lang-python/dist/index.js'),
      '@codemirror/autocomplete': nm('@codemirror/autocomplete/dist/index.js'),
      '@lezer/highlight':         nm('@lezer/highlight/dist/index.js'),
      '@lezer/common':            nm('@lezer/common/dist/index.js'),
      '@lezer/python':            nm('@lezer/python/dist/index.js'),
      'codemirror':               nm('codemirror/dist/index.js'),

      // Transitive deps used by the above packages
      '@marijn/find-cluster-break': nm('@marijn/find-cluster-break/src/index.js'),
      'style-mod':                  nm('style-mod/src/style-mod.js'),
      'w3c-keyname':                nm('w3c-keyname/index.js'),
      'crelt':                      nm('crelt/index.js'),
    },
  },

  // Local backend proxy for fleet-rlm integration.
  // Env-driven direct URLs remain supported and are preferred for deployed targets.
  server: {
    proxy: {
      '/health': 'http://localhost:8000',
      '/ready': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/tasks': 'http://localhost:8000',
      '/sessions': 'http://localhost:8000',
      '/ws/chat': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],

  // Force Vite to pre-bundle CodeMirror packages and their transitive deps
  // (fixes resolution in strict pnpm envs where transitive deps live only in .pnpm/ store)
  optimizeDeps: {
    include: [
      // Ensure a single React instance across all pre-bundled deps
      'react',
      'react-dom',
      'react-router',
      // Primary CodeMirror packages
      '@codemirror/state',
      '@codemirror/view',
      '@codemirror/commands',
      '@codemirror/language',
      '@codemirror/lang-python',
      '@codemirror/autocomplete',
      '@lezer/highlight',
      '@lezer/common',
      '@lezer/python',
      'codemirror',
      // Transitive deps needed by the above (not hoisted by pnpm strict mode)
      '@marijn/find-cluster-break',
      'style-mod',
      'w3c-keyname',
      'crelt',
    ],
  },

  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    exclude: ['tests/e2e/**'],
  },
})
