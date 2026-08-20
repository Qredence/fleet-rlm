/**
 * Pins the shared wire-coercion contract (P33/QRE-199). The two record
 * coercions differ deliberately: adapters keep array-including `asRecord`
 * wire semantics while summaries/clients use array-excluding `record`.
 */

import { describe, expect, it } from "vitest";

import { asRecord, int, record, str } from "../coerce.js";

describe("asRecord (array-including wire semantics)", () => {
  it("returns {} for null, undefined, and non-object values", () => {
    expect(asRecord(null)).toEqual({});
    expect(asRecord(undefined)).toEqual({});
    expect(asRecord("x")).toEqual({});
    expect(asRecord(5)).toEqual({});
    expect(asRecord(true)).toEqual({});
  });

  it("returns the same reference for plain objects", () => {
    const value = { phase: "running" };
    expect(asRecord(value)).toBe(value);
  });

  it("returns the same reference for arrays (indices stay observable)", () => {
    const value = ["a", "b"];
    expect(asRecord(value)).toBe(value);
    expect(Object.keys(asRecord(value))).toEqual(["0", "1"]);
  });
});

describe("record (array-excluding payload semantics)", () => {
  it("returns {} for null, undefined, and non-object values", () => {
    expect(record(null)).toEqual({});
    expect(record(undefined)).toEqual({});
    expect(record("x")).toEqual({});
    expect(record(5)).toEqual({});
    expect(record(true)).toEqual({});
  });

  it("returns {} for arrays", () => {
    expect(record(["a", "b"])).toEqual({});
    expect(Object.keys(record(["a", "b"]))).toEqual([]);
  });

  it("returns the same reference for plain objects", () => {
    const value = { prompt_count: 2 };
    expect(record(value)).toBe(value);
  });
});

describe("str (non-empty-only)", () => {
  it("returns undefined for non-strings and the empty string", () => {
    expect(str(undefined)).toBeUndefined();
    expect(str(null)).toBeUndefined();
    expect(str(5)).toBeUndefined();
    expect(str("")).toBeUndefined();
  });

  it("returns strings verbatim, including whitespace-only values", () => {
    expect(str("x")).toBe("x");
    expect(str(" ")).toBe(" ");
  });
});

describe("int (integer-only, undefined sentinel)", () => {
  it("returns integers, including zero and negatives", () => {
    expect(int(0)).toBe(0);
    expect(int(3)).toBe(3);
    expect(int(-3)).toBe(-3);
  });

  it("returns undefined (not null) for non-integer or non-number values", () => {
    expect(int(1.5)).toBeUndefined();
    expect(int(Number.NaN)).toBeUndefined();
    expect(int(Number.POSITIVE_INFINITY)).toBeUndefined();
    expect(int("3")).toBeUndefined();
    expect(int(null)).toBeUndefined();
    expect(int(undefined)).toBeUndefined();
  });
});
