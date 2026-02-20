/**
 * CodeMirror barrel module — re-exports all CodeMirror packages needed
 * by useCodeMirror.
 *
 * This file uses **static** imports which Vite resolves through its
 * normal dependency pre-bundling pipeline.  The consuming hook then
 * dynamically imports THIS local file (`import('@/lib/data/codemirror-modules')`),
 * keeping CodeMirror out of the initial synchronous bundle while giving
 * Vite a resolvable local path for the dynamic import.
 *
 * Why this pattern?
 *   - `import('@codemirror/state')` (string-literal dynamic) fails because
 *     Vite's `import-analysis` plugin can't resolve bare package specifiers
 *     in dynamic imports within the Figma Make pnpm strict environment.
 *   - `import(variable)` (opaque dynamic) fails because the browser
 *     receives a bare specifier it can't resolve (no import map).
 *   - A local barrel with static imports + dynamic import of the barrel
 *     combines the best of both: Vite pre-bundles the static deps, and
 *     the browser only sees a Vite-rewritten local file URL.
 */

// ── @codemirror/state ───────────────────────────────────────────────
export {
  EditorState,
  StateEffect,
  StateField,
  Compartment,
} from "@codemirror/state";

// ── @codemirror/view ────────────────────────────────────────────────
export {
  EditorView,
  lineNumbers,
  highlightActiveLine,
  keymap,
  ViewPlugin,
  Decoration,
  WidgetType,
} from "@codemirror/view";

// ── @codemirror/commands ────────────────────────────────────────────
export {
  defaultKeymap,
  indentWithTab,
  history,
  historyKeymap,
} from "@codemirror/commands";

// ── @codemirror/lang-python ─────────────────────────────────────────
export { python } from "@codemirror/lang-python";

// ── @codemirror/language ────────────────────────────────────────────
export {
  HighlightStyle,
  syntaxHighlighting,
  bracketMatching,
  indentOnInput,
} from "@codemirror/language";

// ── @lezer/highlight ────────────────────────────────────────────────
export { tags } from "@lezer/highlight";
