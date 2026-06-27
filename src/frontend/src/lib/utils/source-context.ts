const PATH_CANDIDATE_RE = /(?:^|[\s("'`])((?:~\/|\/|\.\.\/|\.\/)[^\s"'`<>()[\]{}]+)/g;

function stripTrailingPathPunctuation(value: string): string {
  return value.replace(/[.,!?;:]+$/g, "").replace(/["'`)\]}]+$/g, "");
}

export function parseContextPaths(value: string): string[] {
  return value
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function detectContextPaths(value: string): string[] {
  const detected = new Set<string>();
  const matches = value.matchAll(PATH_CANDIDATE_RE);
  for (const match of matches) {
    const candidate = stripTrailingPathPunctuation(match[1] ?? "").trim();
    if (!candidate || candidate === "/" || candidate.includes("://")) {
      continue;
    }
    // Drop bare URL-route-like tokens (e.g. "/docs", "/api", "/v1") that the
    // regex captures from prose but which almost never refer to real host
    // files. Keep multi-segment absolutes, home-relative (~/), relative
    // (./, ../), and anything with a file extension.
    const tail = candidate.slice(1);
    if (candidate.startsWith("/") && !tail.includes("/") && !/\.[A-Za-z0-9]+$/.test(tail)) {
      continue;
    }
    detected.add(candidate);
  }
  return [...detected];
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
