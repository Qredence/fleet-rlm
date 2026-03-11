const DAYTONA_REPO_HOSTS = new Set([
  "github.com",
  "gitlab.com",
  "bitbucket.org",
]);

const DAYTONA_REPO_CANDIDATE_RE = /@?https:\/\/[^\s)>\]}]+/gi;

export type DaytonaRepoDetectionSource =
  | "manual"
  | "prompt_url"
  | "prompt_mention";

export interface DetectedDaytonaRepoContext {
  repoUrl: string;
  source: Exclude<DaytonaRepoDetectionSource, "manual">;
  matchedText: string;
}

export interface ResolvedDaytonaRepoContext {
  repoUrl: string;
  source: DaytonaRepoDetectionSource;
  matchedText?: string;
  detected?: DetectedDaytonaRepoContext | null;
}

function stripTrailingPunctuation(value: string): string {
  return value.replace(/[.,!?;:]+$/g, "");
}

export function normalizeDaytonaRepoUrl(value: string): string | null {
  const trimmed = stripTrailingPunctuation(value.trim()).replace(/^@/, "");
  if (!trimmed) return null;

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }

  if (parsed.protocol !== "https:") return null;
  const host = parsed.hostname.toLowerCase();
  if (!DAYTONA_REPO_HOSTS.has(host)) return null;

  const segments = parsed.pathname
    .split("/")
    .map((segment) => segment.trim())
    .filter(Boolean);
  if (segments.length < 2) return null;

  const owner = segments[0]!;
  const repo = segments[1]!.replace(/\.git$/i, "");
  if (!owner || !repo) return null;

  return `https://${host}/${owner}/${repo}`;
}

export function detectDaytonaRepoContext(
  value: string,
): DetectedDaytonaRepoContext | null {
  const matches = value.matchAll(DAYTONA_REPO_CANDIDATE_RE);
  for (const match of matches) {
    const matchedText = match[0];
    if (!matchedText) continue;
    const repoUrl = normalizeDaytonaRepoUrl(matchedText);
    if (!repoUrl) continue;
    return {
      repoUrl,
      source: matchedText.startsWith("@") ? "prompt_mention" : "prompt_url",
      matchedText,
    };
  }
  return null;
}

export function resolveDaytonaRepoContext({
  manualRepoUrl,
  promptText,
}: {
  manualRepoUrl: string;
  promptText: string;
}): ResolvedDaytonaRepoContext | null {
  const hasManualOverride = manualRepoUrl.trim().length > 0;
  const normalizedManualRepoUrl = normalizeDaytonaRepoUrl(manualRepoUrl);
  const detected = detectDaytonaRepoContext(promptText);

  if (normalizedManualRepoUrl) {
    return {
      repoUrl: normalizedManualRepoUrl,
      source: "manual",
      detected,
    };
  }

  if (hasManualOverride) {
    return null;
  }

  if (!detected) return null;
  return {
    repoUrl: detected.repoUrl,
    source: detected.source,
    matchedText: detected.matchedText,
    detected,
  };
}
