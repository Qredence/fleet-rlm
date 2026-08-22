import { stripTerminalSequences } from "@earendil-works/pi-tui";

/** Remove terminal control sequences while preserving Markdown newlines and tabs. */
export function terminalSafeText(value: string): string {
  let safe = "";
  for (const character of stripTerminalSequences(value)) {
    if (/\p{Cf}/u.test(character)) continue;
    const codePoint = character.codePointAt(0) ?? 0;
    if (
      codePoint === 9 ||
      codePoint === 10 ||
      (codePoint > 31 && codePoint !== 127 && codePoint < 0x80)
    ) {
      safe += character;
      continue;
    }
    if (codePoint > 0x9f) safe += character;
  }
  return safe;
}

/** Normalize untrusted single-line metadata before it reaches the terminal. */
export function terminalSafeLine(value: string): string {
  return terminalSafeText(value)
    .replaceAll(/[\p{Cc}\p{Zl}\p{Zp}]+/gu, " ")
    .replaceAll(/\s+/gu, " ")
    .trim();
}

const graphemeSegmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/** Remove the last grapheme cluster (safe for emoji, ZWJ sequences, combining marks). */
export function dropLastGrapheme(value: string): string {
  const segments = Array.from(graphemeSegmenter.segment(value), ({ segment }) => segment);
  return segments.slice(0, -1).join("");
}

/**
 * Determines whether text spans multiple lines after normalizing line endings and removing trailing whitespace.
 *
 * @param value - The text to inspect
 * @returns `true` if the text contains multiple lines, `false` otherwise.
 */
export function hasMultipleLines(value: string): boolean {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trimEnd().split("\n").length > 1;
}
