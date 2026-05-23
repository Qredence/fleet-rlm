export function normalizeDaytonaMode(mode?: string | null): string | undefined {
  const trimmed = mode?.trim();
  if (!trimmed) return undefined;
  return trimmed;
}
