export function normalizeDaytonaMode(mode?: string | null): string | undefined {
  const trimmed = mode?.trim();
  if (!trimmed) return undefined;
  if (trimmed === "recursive_rlm") {
    return "host_loop_rlm";
  }
  return trimmed;
}
