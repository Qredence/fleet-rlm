import type { CSSProperties } from "react";

// ── Typography helpers ──────────────────────────────────────────────
// Every key maps to an inline-style object whose values reference CSS
// custom properties from /src/styles/theme.css.  This lets the entire
// app's typography be re-themed by editing a single CSS file.
//
// Usage:
//   import { typo } from '@/lib/config/typo';
//   <h2 style={typo.h2}>Title</h2>
//   <p  style={typo.base}>Body</p>

export const typo: Record<string, CSSProperties> = {
  h1: {
    fontSize: "var(--text-h1)",
    fontWeight: "var(--font-weight-semibold)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.2",
  },
  display: {
    fontSize: "var(--text-display)",
    fontWeight: "var(--font-weight-regular)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.2",
  },
  h2: {
    fontSize: "var(--text-h2)",
    fontWeight: "var(--font-weight-semibold)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.2",
  },
  h3: {
    fontSize: "var(--text-h3)",
    fontWeight: "var(--font-weight-semibold)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.4",
  },
  h4: {
    fontSize: "var(--text-h4)",
    fontWeight: "var(--font-weight-medium)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.4",
  },
  base: {
    fontSize: "var(--text-base)",
    fontWeight: "var(--font-weight-regular)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.5",
  },
  label: {
    fontSize: "var(--text-label)",
    fontWeight: "var(--font-weight-medium)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.4",
  },
  labelRegular: {
    fontSize: "var(--text-label)",
    fontWeight: "var(--font-weight-regular)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.6",
  },
  caption: {
    fontSize: "var(--text-caption)",
    fontWeight: "var(--font-weight-regular)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.4",
  },
  helper: {
    fontSize: "var(--text-helper)",
    fontWeight: "var(--font-weight-regular)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.3",
  },
  micro: {
    fontSize: "var(--text-micro)",
    fontWeight: "var(--font-weight-regular)",
    fontFamily: "var(--font-family)",
    lineHeight: "1.3",
  },
  mono: {
    fontSize: "var(--text-caption)",
    fontFamily: "var(--font-family-mono)",
    fontWeight: "var(--font-weight-regular)",
    lineHeight: "1.5",
  },
};
