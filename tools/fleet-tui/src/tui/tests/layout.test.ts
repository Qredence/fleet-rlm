import { describe, expect, it } from "vitest";

import { COMPACT_COLUMNS, COMPACT_ROWS, isCompact, railPadding } from "../layout.js";

describe("layout breakpoints", () => {
  it("treats short or narrow terminals as compact", () => {
    expect(isCompact(COMPACT_ROWS - 1, 80)).toBe(true);
    expect(isCompact(24, COMPACT_COLUMNS - 1)).toBe(true);
    expect(isCompact(24, 80)).toBe(false);
  });

  it("scales rail padding down with the available width", () => {
    expect(railPadding(80)).toBe(2);
    expect(railPadding(4)).toBe(1);
    expect(railPadding(2)).toBe(0);
  });
});
