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
    fontSize: "var(--font-heading-2xl-size)",
    fontWeight: "var(--font-heading-2xl-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-heading-2xl-line-height)",
    letterSpacing: "var(--font-heading-2xl-tracking)",
  },
  display: {
    fontSize: "var(--font-heading-xl-size)",
    fontWeight: "var(--font-text-md-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-heading-xl-line-height)",
    letterSpacing: "var(--font-heading-xl-tracking)",
  },
  h2: {
    fontSize: "var(--font-heading-lg-size)",
    fontWeight: "var(--font-heading-lg-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-heading-lg-line-height)",
    letterSpacing: "var(--font-heading-lg-tracking)",
  },
  h3: {
    fontSize: "var(--font-heading-sm-size)",
    fontWeight: "var(--font-heading-sm-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-heading-sm-line-height)",
    letterSpacing: "var(--font-heading-sm-tracking)",
  },
  h4: {
    fontSize: "var(--font-heading-xs-size)",
    fontWeight: "var(--font-weight-medium)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-heading-xs-line-height)",
    letterSpacing: "var(--font-heading-xs-tracking)",
  },
  base: {
    fontSize: "var(--font-text-md-size)",
    fontWeight: "var(--font-text-md-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-text-md-line-height)",
    letterSpacing: "var(--font-text-md-tracking)",
  },
  label: {
    fontSize: "var(--font-text-sm-size)",
    fontWeight: "var(--font-weight-medium)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-text-sm-line-height)",
    letterSpacing: "var(--font-text-sm-tracking)",
  },
  labelRegular: {
    fontSize: "var(--font-text-sm-size)",
    fontWeight: "var(--font-text-sm-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-text-sm-line-height)",
    letterSpacing: "var(--font-text-sm-tracking)",
  },
  caption: {
    fontSize: "var(--font-text-xs-size)",
    fontWeight: "var(--font-text-xs-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-text-xs-line-height)",
    letterSpacing: "var(--font-text-xs-tracking)",
  },
  helper: {
    fontSize: "var(--font-text-2xs-size)",
    fontWeight: "var(--font-text-2xs-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-text-2xs-line-height)",
    letterSpacing: "var(--font-text-2xs-tracking)",
  },
  micro: {
    fontSize: "var(--font-text-3xs-size)",
    fontWeight: "var(--font-text-3xs-weight)",
    fontFamily: "var(--font-sans)",
    lineHeight: "var(--font-text-3xs-line-height)",
    letterSpacing: "var(--font-text-3xs-tracking)",
  },
  mono: {
    fontSize: "var(--font-text-xs-size)",
    fontFamily: "var(--font-mono)",
    fontWeight: "var(--font-text-xs-weight)",
    lineHeight: "var(--font-text-xs-line-height)",
    letterSpacing: "var(--font-text-xs-tracking)",
  },
};
