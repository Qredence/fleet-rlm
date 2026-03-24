/**
 * Environment variable utilities.
 */

/** Trims a string env var, returning empty string if undefined. */
export function trimOrEmpty(value: string | undefined): string {
  return value?.trim() ?? "";
}

/** Safe JSON parse — returns null on any error. */
export function parseStoredJson(raw: string | null): unknown {
  try {
    return raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    return null;
  }
}

export function parseBool(value: string | undefined, fallback: boolean): boolean {
  if (value == null) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === "true" || normalized === "1" || normalized === "yes") {
    return true;
  }
  if (normalized === "false" || normalized === "0" || normalized === "no") {
    return false;
  }
  return fallback;
}
