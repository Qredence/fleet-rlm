/**
 * Shared wire-side coercion primitives (P33/QRE-199): the single home for
 * unknown→typed narrowing used by the wire adapters and summaries. The two
 * record coercions differ deliberately: `asRecord` keeps the adapters'
 * semantics (any non-null object, arrays included) while `record` excludes
 * arrays for summary/client payload reads.
 */

export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function str(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function int(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) ? value : undefined;
}

export function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
