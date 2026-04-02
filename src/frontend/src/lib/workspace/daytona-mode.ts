export function normalizeDaytonaMode(mode?: string | null): string | undefined {
  const trimmed = mode?.trim();
  if (!trimmed) return undefined;
  // Normalise legacy mode strings to the canonical "daytona_pilot" value.
  if (trimmed === "recursive_rlm" || trimmed === "host_loop_rlm") {
    return "daytona_pilot";
  }
  return trimmed;
}
