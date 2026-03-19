function titleCaseWords(value: string): string {
  return value
    .split(/\s+/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function formatDaytonaModeLabel(mode?: string | null): string | undefined {
  const trimmed = normalizeDaytonaMode(mode);
  if (!trimmed) return undefined;

  if (trimmed === "host_loop_rlm") {
    return "Host-loop REPL";
  }

  return titleCaseWords(trimmed.replace(/[_-]+/g, " "));
}

export function normalizeDaytonaMode(mode?: string | null): string | undefined {
  const trimmed = mode?.trim();
  if (!trimmed) return undefined;
  if (trimmed === "recursive_rlm") {
    return "host_loop_rlm";
  }
  return trimmed;
}
