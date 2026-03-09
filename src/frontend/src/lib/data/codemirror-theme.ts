/**
 * Custom CodeMirror theme that uses the project's CSS design tokens.
 *
 * All colours reference `var(--*)` custom properties so the editor
 * automatically adapts to light/dark mode and any theme overrides.
 *
 * Typography uses canonical Apps SDK tokens (`var(--font-mono)` and
 * `var(--font-text-xs-size)`) to match the shared mono text scale.
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
        fontSize: "var(--font-text-xs-size)",
        fontFamily: "var(--font-mono)",
        fontWeight: "var(--font-text-xs-weight)",
        lineHeight: "var(--font-text-xs-line-height)",
        color: "var(--color-text)",
        backgroundColor: "var(--color-surface-elevated)",
        height: "100%",
      },
      ".cm-content": {
        caretColor: "var(--color-background-info-solid)",
        padding: "12px 0",
        fontFamily: "var(--font-mono)",
      },
      ".cm-cursor, .cm-dropCursor": {
        borderLeftColor: "var(--color-background-info-solid)",
        borderLeftWidth: "2px",
      },
      "&.cm-focused .cm-cursor": {
        borderLeftColor: "var(--color-background-info-solid)",
      },
      "&.cm-focused": {
        outline: "none",
      },
      ".cm-selectionBackground, ::selection": {
        backgroundColor:
          "color-mix(in srgb, var(--color-background-info-solid) 20%, transparent)",
      },
      "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground":
        {
          backgroundColor:
            "color-mix(in srgb, var(--color-background-info-solid) 25%, transparent)",
        },
      ".cm-activeLine": {
        backgroundColor:
          "color-mix(in srgb, var(--color-background-primary-soft) 40%, transparent)",
      },
      ".cm-gutters": {
        backgroundColor: "var(--color-surface-secondary)",
        color: "var(--color-text-secondary)",
        border: "none",
        borderRight: "1px solid var(--color-border-subtle)",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-text-xs-size)",
      },
      ".cm-activeLineGutter": {
        backgroundColor:
          "color-mix(in srgb, var(--color-background-primary-soft) 50%, transparent)",
        color: "var(--color-text)",
      },
      ".cm-lineNumbers .cm-gutterElement": {
        padding: "0 12px 0 8px",
        minWidth: "3em",
      },
      ".cm-foldPlaceholder": {
        backgroundColor: "var(--color-background-primary-soft)",
        border: "1px solid var(--color-border-subtle)",
        color: "var(--color-text-secondary)",
      },
      ".cm-tooltip": {
        backgroundColor: "var(--color-surface-elevated)",
        color: "var(--color-text)",
        border: "1px solid var(--color-border-subtle)",
        borderRadius: "var(--radius-lg)",
      },
      ".cm-tooltip-autocomplete": {
        "& > ul > li": {
          fontFamily: "var(--font-mono)",
        },
        "& > ul > li[aria-selected]": {
          backgroundColor: "var(--color-background-info-solid)",
          color: "var(--color-text-info-solid)",
        },
      },
      ".cm-scroller": {
        overflow: "auto",
        fontFamily: "var(--font-mono)",
      },
      ".cm-matchingBracket": {
        backgroundColor:
          "color-mix(in srgb, var(--color-background-info-solid) 20%, transparent)",
        outline:
          "1px solid color-mix(in srgb, var(--color-background-info-solid) 40%, transparent)",
      },
      ".cm-nonmatchingBracket": {
        color: "var(--color-text-danger)",
      },
      ".cm-searchMatch": {
        backgroundColor: "color-mix(in srgb, var(--chart-5) 30%, transparent)",
        outline:
          "1px solid color-mix(in srgb, var(--chart-5) 50%, transparent)",
      },
      ".cm-searchMatch.cm-searchMatch-selected": {
        backgroundColor:
          "color-mix(in srgb, var(--color-background-info-solid) 30%, transparent)",
      },
    },
    { dark: false },
  );
}

// ── Syntax highlighting factory ─────────────────────────────────────

export function createSyntaxHighlighting(CM: CMBarrel) {
  const highlightStyles = CM.HighlightStyle.define([
    // Keywords (import, from, def, class, if, for, etc.)
    { tag: CM.tags.keyword, color: "var(--color-text-discovery)" },
    { tag: CM.tags.controlKeyword, color: "var(--color-text-discovery)" },
    { tag: CM.tags.definitionKeyword, color: "var(--color-text-discovery)" },
    { tag: CM.tags.moduleKeyword, color: "var(--color-text-discovery)" },
    { tag: CM.tags.operatorKeyword, color: "var(--color-text-discovery)" },

    // Strings
    { tag: CM.tags.string, color: "var(--color-text-success)" },
    {
      tag: CM.tags.special(CM.tags.string),
      color: "var(--color-text-success)",
    },

    // Numbers
    { tag: CM.tags.number, color: "var(--color-text-caution)" },
    { tag: CM.tags.integer, color: "var(--color-text-caution)" },
    { tag: CM.tags.float, color: "var(--color-text-caution)" },

    // Comments
    { tag: CM.tags.comment, color: "var(--color-text-secondary)" },
    { tag: CM.tags.lineComment, color: "var(--color-text-secondary)" },
    { tag: CM.tags.blockComment, color: "var(--color-text-secondary)" },

    // Functions & definitions
    {
      tag: CM.tags.function(CM.tags.variableName),
      color: "var(--color-text-info)",
    },
    {
      tag: CM.tags.definition(CM.tags.variableName),
      color: "var(--color-text)",
    },

    // Built-ins
    {
      tag: CM.tags.standard(CM.tags.variableName),
      color: "var(--color-text-caution)",
    },

    // Variables & properties
    { tag: CM.tags.variableName, color: "var(--color-text)" },
    { tag: CM.tags.propertyName, color: "var(--color-text-info)" },

    // Operators and punctuation
    { tag: CM.tags.operator, color: "var(--color-text-secondary)" },
    { tag: CM.tags.punctuation, color: "var(--color-text-secondary)" },
    { tag: CM.tags.bracket, color: "var(--color-text)" },
    { tag: CM.tags.paren, color: "var(--color-text)" },
    { tag: CM.tags.brace, color: "var(--color-text)" },
    { tag: CM.tags.squareBracket, color: "var(--color-text)" },

    // Booleans, None
    { tag: CM.tags.bool, color: "var(--color-text-caution)" },
    { tag: CM.tags.null, color: "var(--color-text-caution)" },

    // Types & classes
    { tag: CM.tags.typeName, color: "var(--color-text-caution)" },
    { tag: CM.tags.className, color: "var(--color-text-caution)" },

    // Decorators
    { tag: CM.tags.meta, color: "var(--color-text-discovery)" },

    // Self parameter
    { tag: CM.tags.self, color: "var(--color-text-discovery)" },
  ]);

  return CM.syntaxHighlighting(highlightStyles);
}
