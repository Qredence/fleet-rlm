/**
 * Custom CodeMirror theme that uses the project's CSS design tokens.
 *
 * All colours reference `var(--*)` custom properties so the editor
 * automatically adapts to light/dark mode and any theme overrides.
 *
 * Typography uses `var(--font-family-mono)` and `var(--text-caption)`
 * to match the design system's mono text scale.
 *
 * The factory functions accept the barrel-module namespace object
 * (`typeof import('@/lib/data/codemirror-modules')`) so they work with the
 * dynamic-import pattern used by `useCodeMirror`.
 */

// ── Types ───────────────────────────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type CMBarrel = any;

// ── Editor chrome theme factory ─────────────────────────────────────

export function createEditorTheme(CM: CMBarrel) {
  return CM.EditorView.theme(
    {
      "&": {
        fontSize: "var(--text-caption)",
        fontFamily: "var(--font-family-mono)",
        fontWeight: "var(--font-weight-regular)",
        lineHeight: "var(--line-height-loose)",
        color: "var(--foreground)",
        backgroundColor: "var(--card)",
        height: "100%",
      },
      ".cm-content": {
        caretColor: "var(--accent)",
        padding: "12px 0",
        fontFamily: "var(--font-family-mono)",
      },
      ".cm-cursor, .cm-dropCursor": {
        borderLeftColor: "var(--accent)",
        borderLeftWidth: "2px",
      },
      "&.cm-focused .cm-cursor": {
        borderLeftColor: "var(--accent)",
      },
      "&.cm-focused": {
        outline: "none",
      },
      ".cm-selectionBackground, ::selection": {
        backgroundColor: "color-mix(in srgb, var(--accent) 20%, transparent)",
      },
      "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground":
        {
          backgroundColor: "color-mix(in srgb, var(--accent) 25%, transparent)",
        },
      ".cm-activeLine": {
        backgroundColor: "color-mix(in srgb, var(--muted) 40%, transparent)",
      },
      ".cm-gutters": {
        backgroundColor: "var(--bg-elevated-primary)",
        color: "var(--muted-foreground)",
        border: "none",
        borderRight: "1px solid var(--border-subtle)",
        fontFamily: "var(--font-family-mono)",
        fontSize: "var(--text-caption)",
      },
      ".cm-activeLineGutter": {
        backgroundColor: "color-mix(in srgb, var(--muted) 50%, transparent)",
        color: "var(--foreground)",
      },
      ".cm-lineNumbers .cm-gutterElement": {
        padding: "0 12px 0 8px",
        minWidth: "3em",
      },
      ".cm-foldPlaceholder": {
        backgroundColor: "var(--muted)",
        border: "1px solid var(--border-subtle)",
        color: "var(--muted-foreground)",
      },
      ".cm-tooltip": {
        backgroundColor: "var(--popover)",
        color: "var(--popover-foreground)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius)",
      },
      ".cm-tooltip-autocomplete": {
        "& > ul > li": {
          fontFamily: "var(--font-family-mono)",
        },
        "& > ul > li[aria-selected]": {
          backgroundColor: "var(--accent)",
          color: "var(--accent-foreground)",
        },
      },
      ".cm-scroller": {
        overflow: "auto",
        fontFamily: "var(--font-family-mono)",
      },
      ".cm-matchingBracket": {
        backgroundColor: "color-mix(in srgb, var(--accent) 20%, transparent)",
        outline: "1px solid color-mix(in srgb, var(--accent) 40%, transparent)",
      },
      ".cm-nonmatchingBracket": {
        color: "var(--destructive)",
      },
      ".cm-searchMatch": {
        backgroundColor: "color-mix(in srgb, var(--chart-5) 30%, transparent)",
        outline:
          "1px solid color-mix(in srgb, var(--chart-5) 50%, transparent)",
      },
      ".cm-searchMatch.cm-searchMatch-selected": {
        backgroundColor: "color-mix(in srgb, var(--accent) 30%, transparent)",
      },
    },
    { dark: false },
  );
}

// ── Syntax highlighting factory ─────────────────────────────────────

export function createSyntaxHighlighting(CM: CMBarrel) {
  const highlightStyles = CM.HighlightStyle.define([
    // Keywords (import, from, def, class, if, for, etc.)
    { tag: CM.tags.keyword, color: "var(--chart-2)" },
    { tag: CM.tags.controlKeyword, color: "var(--chart-2)" },
    { tag: CM.tags.definitionKeyword, color: "var(--chart-2)" },
    { tag: CM.tags.moduleKeyword, color: "var(--chart-2)" },
    { tag: CM.tags.operatorKeyword, color: "var(--chart-2)" },

    // Strings
    { tag: CM.tags.string, color: "var(--chart-3)" },
    { tag: CM.tags.special(CM.tags.string), color: "var(--chart-3)" },

    // Numbers
    { tag: CM.tags.number, color: "var(--chart-4)" },
    { tag: CM.tags.integer, color: "var(--chart-4)" },
    { tag: CM.tags.float, color: "var(--chart-4)" },

    // Comments
    { tag: CM.tags.comment, color: "var(--muted-foreground)" },
    { tag: CM.tags.lineComment, color: "var(--muted-foreground)" },
    { tag: CM.tags.blockComment, color: "var(--muted-foreground)" },

    // Functions & definitions
    {
      tag: CM.tags.function(CM.tags.variableName),
      color: "var(--chart-1)",
    },
    {
      tag: CM.tags.definition(CM.tags.variableName),
      color: "var(--foreground)",
    },

    // Built-ins
    { tag: CM.tags.standard(CM.tags.variableName), color: "var(--chart-5)" },

    // Variables & properties
    { tag: CM.tags.variableName, color: "var(--foreground)" },
    { tag: CM.tags.propertyName, color: "var(--chart-1)" },

    // Operators and punctuation
    { tag: CM.tags.operator, color: "var(--muted-foreground)" },
    { tag: CM.tags.punctuation, color: "var(--muted-foreground)" },
    { tag: CM.tags.bracket, color: "var(--foreground)" },
    { tag: CM.tags.paren, color: "var(--foreground)" },
    { tag: CM.tags.brace, color: "var(--foreground)" },
    { tag: CM.tags.squareBracket, color: "var(--foreground)" },

    // Booleans, None
    { tag: CM.tags.bool, color: "var(--chart-4)" },
    { tag: CM.tags.null, color: "var(--chart-4)" },

    // Types & classes
    { tag: CM.tags.typeName, color: "var(--chart-5)" },
    { tag: CM.tags.className, color: "var(--chart-5)" },

    // Decorators
    { tag: CM.tags.meta, color: "var(--chart-2)" },

    // Self parameter
    { tag: CM.tags.self, color: "var(--chart-2)" },
  ]);

  return CM.syntaxHighlighting(highlightStyles);
}
