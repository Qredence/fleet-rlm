export const MONO_BASE_STYLE = {
  fontSize: "var(--font-text-sm-size)",
  fontWeight: "var(--font-text-sm-weight)",
  fontFamily: "var(--font-mono)",
  lineHeight: "var(--font-text-sm-line-height)",
  letterSpacing: "var(--font-text-sm-tracking)",
} as const;

export const MONO_BASE_MEDIUM_STYLE = {
  ...MONO_BASE_STYLE,
  fontWeight: "var(--font-weight-medium)",
} as const;

export const DISPLAY_TITLE_STYLE = {
  fontSize: "var(--font-heading-xl-size)",
  fontFamily: "var(--font-sans)",
  fontWeight: "var(--font-weight-medium)",
  lineHeight: "var(--font-heading-xl-line-height)",
  letterSpacing: "var(--font-heading-xl-tracking)",
  textWrap: "balance",
} as const;

export const DISPLAY_SUBTITLE_STYLE = {
  fontSize: "var(--font-heading-lg-size)",
  fontFamily: "var(--font-sans)",
  fontWeight: "var(--font-weight-normal)",
  lineHeight: "var(--font-heading-lg-line-height)",
  letterSpacing: "var(--font-heading-lg-tracking)",
  textWrap: "balance",
  opacity: "0.6",
} as const;

export const SYSTEM_MESSAGE_STYLE = {
  fontSize: "var(--font-text-3xs-size)",
  fontFamily: "var(--font-sans)",
  lineHeight: "var(--font-text-3xs-line-height)",
  letterSpacing: "var(--font-text-3xs-tracking)",
  fontWeight: "var(--font-weight-semibold)",
} as const;
