/**
 * SuggestionIcons — lightweight SVG icon components used by SuggestionChip
 * in the chat welcome state. Extracted here so they can be reused in any
 * surface that renders suggestion prompts (e.g. empty-state panels, onboarding
 * flows) without pulling in the full ChatMessageList module.
 *
 * All stroke colours reference CSS custom properties from theme.css so they
 * automatically adapt to light/dark mode without any JS involvement.
 *
 * Plain function declarations — `const + forwardRef` crashes HMR in
 * Figma Make preview.
 */

import svgPaths from "@/imports/svg-synwn0xtnf";

// ── Bolt ─────────────────────────────────────────────────────────────

/** Lightning bolt — "Create / generate" affordance. */
export function SuggestionIconBolt() {
  return (
    <svg
      className="size-4 shrink-0"
      fill="none"
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <path
        d={svgPaths.p1d59db00}
        stroke="var(--muted-foreground)"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.33333"
      />
    </svg>
  );
}

// ── Tune / Sliders ────────────────────────────────────────────────────

/** Sliders / tune — "Configure / build" affordance. */
export function SuggestionIconTune() {
  return (
    <svg
      className="size-4 shrink-0"
      fill="none"
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <g clipPath="url(#suggestion-tune-clip)">
        <path
          d={svgPaths.pb3f4d00}
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d={svgPaths.p2bdb5600}
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M3.33333 4V6.66667"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M12.6667 9.33333V12"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M6.66667 1.33333V2.66667"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M4.66667 5.33333H2"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M14 10.6667H11.3333"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M7.33333 2H6"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
      </g>
      <defs>
        <clipPath id="suggestion-tune-clip">
          <rect fill="white" height="16" width="16" />
        </clipPath>
      </defs>
    </svg>
  );
}

// ── Sparkle ───────────────────────────────────────────────────────────

/** Sparkle / magic — "Design / imagine" affordance. */
export function SuggestionIconSparkle() {
  return (
    <svg
      className="size-4 shrink-0"
      fill="none"
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <g clipPath="url(#suggestion-sparkle-clip)">
        <path
          d={svgPaths.p874e300}
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M13.3333 2V4.66667"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M14.6667 3.33333H12"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M2.66667 11.3333V12.6667"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
        <path
          d="M3.33333 12H2"
          stroke="var(--muted-foreground)"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.33333"
        />
      </g>
      <defs>
        <clipPath id="suggestion-sparkle-clip">
          <rect fill="white" height="16" width="16" />
        </clipPath>
      </defs>
    </svg>
  );
}
