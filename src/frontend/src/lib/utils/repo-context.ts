const REPO_HOSTS = new Set(["github.com", "gitlab.com", "bitbucket.org"]);

const REPO_CANDIDATE_RE = /@?https:\/\/[^\s)>\]}]+/gi;

export type RepoDetectionSource = "manual" | "prompt_url" | "prompt_mention";

export interface DetectedRepoContext {
  repoUrl: string;
  repoRef?: string;
  repoRefCandidate?: string;
  source: Exclude<RepoDetectionSource, "manual">;
  matchedText: string;
}

export interface ResolvedRepoContext {
  repoUrl: string;
  repoRef?: string;
  repoRefCandidate?: string;
  source: RepoDetectionSource;
  matchedText?: string;
  detected?: DetectedRepoContext | null;
}

function stripTrailingPunctuation(value: string): string {
  return value.replace(/[.,!?;:]+$/g, "");
}

function parseRepoCandidate(
  value: string,
): { repoUrl: string; repoRef?: string; repoRefCandidate?: string } | null {
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
  if (!REPO_HOSTS.has(host)) return null;

  const segments = parsed.pathname
    .split("/")
    .map((segment) => segment.trim())
    .filter(Boolean);
  if (segments.length < 2) return null;

  const owner = segments[0]!;
  const repo = segments[1]!.replace(/\.git$/i, "");
  if (!owner || !repo) return null;

  let repoRef: string | undefined;
  let repoRefCandidate: string | undefined;
  if (
    segments.length >= 4 &&
    (segments[2]?.toLowerCase() === "tree" ||
      segments[2]?.toLowerCase() === "blob")
  ) {
    const candidate = decodeURIComponent(segments[3] ?? "").trim();
    if (candidate) {
      repoRef = candidate;
    }
    const candidateTail = decodeURIComponent(
      segments.slice(3).join("/"),
    ).trim();
    if (candidateTail) {
      repoRefCandidate = candidateTail;
    }
  }

  return {
    repoUrl: `https://${host}/${owner}/${repo}`,
    repoRef,
    repoRefCandidate,
  };
}

function detectRepoRefHint(value: string): string | undefined {
  const commitMatch = value.match(
    /(?:^|\s)commit\s+([0-9a-f]{7,40})(?=$|[\s.,!?;:])/i,
  );
  if (commitMatch?.[1]) {
    return commitMatch[1];
  }

  const refMatch = value.match(
    /(?:^|\s)(?:branch|ref)\s+([A-Za-z0-9._/-]+)(?=$|[\s.,!?;:])/i,
  );
  if (refMatch?.[1]) {
    const normalized = stripTrailingPunctuation(refMatch[1]);
    return normalized || undefined;
  }

  return undefined;
}

export function normalizeRepoUrl(value: string): string | null {
  return parseRepoCandidate(value)?.repoUrl ?? null;
}

export function detectRepoContext(value: string): DetectedRepoContext | null {
  const matches = value.matchAll(REPO_CANDIDATE_RE);
  for (const match of matches) {
    const matchedText = match[0];
    if (!matchedText) continue;
    const parsed = parseRepoCandidate(matchedText);
    if (!parsed) continue;
    return {
      repoUrl: parsed.repoUrl,
      repoRef: parsed.repoRef ?? detectRepoRefHint(value),
      repoRefCandidate: parsed.repoRefCandidate,
      source: matchedText.startsWith("@") ? "prompt_mention" : "prompt_url",
      matchedText,
    };
  }
  return null;
}

export function resolveRepoContext({
  manualRepoUrl,
  promptText,
}: {
  manualRepoUrl: string;
  promptText: string;
}): ResolvedRepoContext | null {
  const hasManualOverride = manualRepoUrl.trim().length > 0;
  const parsedManualRepoUrl = parseRepoCandidate(manualRepoUrl);
  const detected = detectRepoContext(promptText);

  if (parsedManualRepoUrl) {
    return {
      repoUrl: parsedManualRepoUrl.repoUrl,
      repoRef: parsedManualRepoUrl.repoRef,
      repoRefCandidate: parsedManualRepoUrl.repoRefCandidate,
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
    repoRef: detected.repoRef,
    repoRefCandidate: detected.repoRefCandidate,
    source: detected.source,
    matchedText: detected.matchedText,
    detected,
  };
}
