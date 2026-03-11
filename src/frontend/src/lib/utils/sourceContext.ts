export function parseContextPaths(value: string): string[] {
  return value
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function buildSourceStateLabel({
  hasRepo,
  hasContext,
}: {
  hasRepo: boolean;
  hasContext: boolean;
}): "Repo" | "Repo + local context" | "Local context only" | "Reasoning only" {
  if (hasRepo && hasContext) return "Repo + local context";
  if (hasRepo) return "Repo";
  if (hasContext) return "Local context only";
  return "Reasoning only";
}
