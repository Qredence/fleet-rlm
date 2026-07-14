import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDuration,
  formatTokens,
  formatStructuredResult,
  previewJson,
  redact,
  shortId,
  sliceVisible,
  visibleLength,
  wrapToWidth,
} from "./format.js";

describe("format helpers", () => {
  it("measures visible length ignoring ANSI escapes", () => {
    expect(visibleLength("hello")).toBe(5);
    expect(visibleLength("\x1b[31mhello\x1b[0m")).toBe(5);
  });

  it("slices up to a visible width and preserves ANSI sequences", () => {
    expect(sliceVisible("\x1b[31mhello world\x1b[0m", 5)).toBe("\x1b[31mhello");
  });

  it("wraps lines to the requested width", () => {
    const lines = wrapToWidth("the quick brown fox jumps over the lazy dog", 10);
    expect(lines.length).toBeGreaterThan(1);
    for (const line of lines) expect(visibleLength(line)).toBeLessThanOrEqual(10);
  });

  it("formats duration in mm:ss or h:mm:ss", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(45_000)).toBe("0:45");
    expect(formatDuration(125_000)).toBe("2:05");
    expect(formatDuration(3_725_000)).toBe("1:02:05");
  });

  it("formats bytes and tokens", () => {
    expect(formatBytes(512)).toBe("512B");
    expect(formatBytes(2_048)).toBe("2.0KB");
    expect(formatBytes(5_242_880)).toBe("5.0MB");
    expect(formatTokens(120)).toBe("120");
    expect(formatTokens(1_500)).toBe("1.5k");
    expect(formatTokens(12_500)).toBe("13k");
  });

  it("shortens uuids and leaves short ids untouched", () => {
    expect(shortId("aabbccdd-eeff-0011-2233-445566778899")).toBe("aabb…8899");
    expect(shortId("short")).toBe("short");
  });

  it("redacts sensitive keys defensively", () => {
    expect(redact({ api_key: "x", nested: { password: "y" }, ok: 1 })).toEqual({
      api_key: "[redacted]",
      nested: { password: "[redacted]" },
      ok: 1,
    });
  });

  it("previews JSON without throwing on circular structures", () => {
    expect(previewJson({ a: 1, token: "x" })).toBe('{"a":1,"token":"[redacted]"}');
    const cycle: Record<string, unknown> = {};
    cycle.self = cycle;
    expect(previewJson(cycle)).toBe("[object Object]");
  });

  it.each([
    ["7", { prominent: "7", rows: [] }],
    [{ digit: "7" }, { prominent: "7", rows: [["digit", "7"]] }],
    [
      { digit: "7", verified: true },
      {
        prominent: null,
        rows: [
          ["digit", "7"],
          ["verified", "true"],
        ],
      },
    ],
    [
      ["a", "b"],
      {
        prominent: null,
        rows: [
          ["1", "a"],
          ["2", "b"],
        ],
      },
    ],
    [null, { prominent: "null", rows: [] }],
  ])("formats structured result %j", (value, expected) => {
    expect(formatStructuredResult(value)).toEqual(expected);
  });

  it("redacts nested structured result fallbacks", () => {
    expect(formatStructuredResult({ payload: { token: "secret", count: 2 } })).toEqual({
      prominent: null,
      rows: [["payload", '{\n  "token": "[redacted]",\n  "count": 2\n}']],
    });
  });
});
