/** Shared ANSI helpers for Fleet TUI render tests. */

const SGR = new RegExp(`${String.fromCharCode(27)}\\[[\\d;]*m`, "g");

/** Remove SGR (color/style) escape sequences, leaving the visible text. */
export function stripAnsi(value: string): string {
  return value.replaceAll(SGR, "");
}

/**
 * Whether the text carries any background color (SGR 48).
 *
 * Deliberately mode-agnostic: asserts a surface exists without coupling the
 * test to a specific truecolor/256-color code.
 */
export function hasBackground(value: string): boolean {
  return new RegExp(`${String.fromCharCode(27)}\\[48;`).test(value);
}
