/** Renderer-neutral shared formatters for the Fleet TUI. */

export function formatDuration(ms: number): string {
  if (ms < 0 || !Number.isFinite(ms)) return "0:00";
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 1) return `0:${seconds.toString().padStart(2, "0")}`;
  if (minutes < 60) return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}:${remMinutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "?B";
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)}MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)}GB`;
}

/**
 * Formats a token count using raw numbers or compact `k` and `M` units.
 *
 * @returns The formatted token count, or `"0"` for invalid or negative values.
 */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n < 1000) return `${n}`;
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/**
 * Formats an observed token count for display.
 *
 * @param value - The observed token count, or `null` when unavailable
 * @returns The formatted token count, or an em dash when the value is `null`
 */
export function formatObservedTokens(value: number | null): string {
  return value === null ? "—" : formatTokens(value);
}

/**
 * Shortens an identifier while preserving its leading and trailing characters.
 *
 * @param id - The identifier to shorten
 * @param length - The number of leading and trailing characters to retain
 * @returns The shortened identifier, or an empty string when `id` is empty
 */
export function shortId(id: string, length = 4): string {
  if (!id) return "";
  const clean = id.replaceAll("-", "");
  if (clean.length <= length * 2) return id;
  return `${clean.slice(0, length)}…${clean.slice(-length)}`;
}

/** Strip the `trace:` prefix and shorten a trace ID for display. */
export function shortTraceId(traceId: string | null | undefined): string {
  if (!traceId) return "—";
  const clean = traceId.replace(/^trace:\/?/, "");
  return shortId(clean);
}

const sensitiveKey =
  /(?:api[-_]?key|authorization|credential|password|secret|private[-_]?key|env(?:ironment)?)/i;
const standaloneTokenKey = /(?:^|[-_])token(?:$|[-_])/i;

export function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, nested]) => [
        key,
        sensitiveKey.test(key) || standaloneTokenKey.test(key) ? "[redacted]" : redact(nested),
      ]),
    );
  }
  return value;
}

export type StructuredResultDisplay = {
  prominent: string | null;
  rows: Array<[label: string, value: string]>;
};

/**
 * Converts a scalar value to its string representation.
 *
 * @param value - The value to convert
 * @returns The string representation of a string, number, boolean, or missing value; `null` for complex values
 */
function scalar(value: unknown): string | null {
  // Missing wire values surface as JSON null, never the literal "undefined".
  if (value === null || value === undefined) return "null";
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  return null;
}

/**
 * Converts a value to a display string, redacting sensitive data in serialized complex values.
 *
 * @param value - The value to convert
 * @returns The scalar representation, formatted redacted JSON, or a string fallback
 */
function resultValue(value: unknown): string {
  const simple = scalar(value);
  if (simple !== null) return simple;
  try {
    return JSON.stringify(redact(value), null, 2) ?? "null";
  } catch {
    return String(value);
  }
}

export function formatStructuredResult(value: unknown): StructuredResultDisplay {
  const simple = scalar(value);
  if (simple !== null) return { prominent: simple, rows: [] };
  if (Array.isArray(value)) {
    return {
      prominent: null,
      rows: value.map((item, index) => [`${index + 1}`, resultValue(item)]),
    };
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 1) {
      const [label, nested] = entries[0] as [string, unknown];
      const nestedScalar = scalar(nested);
      if (nestedScalar !== null) {
        return { prominent: nestedScalar, rows: [[label, nestedScalar]] };
      }
    }
    return {
      prominent: null,
      rows: entries.map(([label, nested]) => [label, resultValue(nested)]),
    };
  }
  return { prominent: String(value), rows: [] };
}
