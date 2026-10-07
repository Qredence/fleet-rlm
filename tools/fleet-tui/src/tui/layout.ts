/**
 * Shared responsive breakpoints for the Fleet TUI.
 *
 * One source of truth for the column/row thresholds the chrome, transcript, and
 * presenter surfaces use to shed optional detail. Values are cell counts, so
 * they are comparable with `visibleWidth` and `terminal.rows`.
 */

/** Rows at or below this are "short": the footer drops to its metrics-only form. */
export const COMPACT_ROWS = 14;
/** Columns below this are "narrow": secondary footer zones and hints collapse. */
export const COMPACT_COLUMNS = 60;
/** Minimum columns with room for the full two-space rail gutter. */
export const RAIL_GUTTER_COLUMNS = 4;

/**
 * Whether the footer should drop its separate hint zone.
 *
 * @param rows - Terminal rows
 * @param width - Terminal columns
 * @returns `true` when the terminal is too short or too narrow for two zones
 */
export function isCompact(rows: number, width: number): boolean {
  return rows < COMPACT_ROWS || width < COMPACT_COLUMNS;
}

/**
 * Horizontal padding for a full-width rail line.
 *
 * @param width - Available columns
 * @returns 2 when there is room for the full gutter, 1 for tight widths, 0 otherwise
 */
export function railPadding(width: number): number {
  if (width > RAIL_GUTTER_COLUMNS) return 2;
  if (width > 2) return 1;
  return 0;
}
