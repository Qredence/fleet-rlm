/**
 * Single marker vocabulary for the Fleet TUI.
 *
 * Every trajectory mark lives here so the transcript, chrome, and presenter
 * surfaces share one geometric, monochrome, single-cell glyph set. Emoji are
 * intentionally excluded: their widths vary across terminals and they fight the
 * quiet-console identity. Status colors stay in `theme.statusGlyph`.
 */
export const MARKS = {
  /** A root execution Turn boundary. */
  trajectory: "◇",
  /** A root-level Tool call. */
  tool: "◆",
  /** A recursive child execution. Shares the diamond family with `trajectory`;
   * the card label disambiguates the two. */
  child: "◇",
  /** Generated code. */
  code: "▸",
  /** Interpreter output. */
  output: "▪",
  /** A typed structured result. */
  result: "✦",
  /** A loaded Skill. */
  skill: "◈",
  /** A pinned Attachment. */
  attachment: "▧",
  /** A committed Artifact. */
  artifact: "▤",
  /** Recoverable warning. */
  warning: "!",
  /** Fatal or recoverable error. */
  error: "×",
  /** RLM reasoning. */
  reasoning: "◦",
  /** Editor submit affordance. */
  submit: "›",
  /** Selected state for a multi-select row (shape, not color). */
  checked: "▣",
  /** Unselected state for a multi-select row (shape, not color). */
  unchecked: "▢",
} as const;
