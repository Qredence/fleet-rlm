export type PostHogWebEnv = Record<string, string | boolean | undefined>;

export const PROJECT_POSTHOG_DEFAULT_HOST = "https://eu.i.posthog.com";

// Public PostHog project API keys are safe to ship in frontend code. Keep this
// as an explicit constant so maintainers can set a project-owned default once
// available, while code/tests/documentation all share the same fallback path.
export const PROJECT_POSTHOG_DEFAULT_API_KEY: string | null = null;

export type PostHogKeySource =
  | "canonical_env"
  | "project_default"
  | "none";

export type ResolvedPostHogWebConfig = {
  apiKey: string | null;
  host: string;
  keySource: PostHogKeySource;
};

type ResolveOverrides = {
  projectDefaultApiKey?: string | null;
};

function readNonEmpty(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function resolvePostHogWebConfig(
  env: PostHogWebEnv,
  overrides: ResolveOverrides = {},
): ResolvedPostHogWebConfig {
  const canonical = readNonEmpty(env.VITE_PUBLIC_POSTHOG_API_KEY);
  const projectDefault = readNonEmpty(
    overrides.projectDefaultApiKey ?? PROJECT_POSTHOG_DEFAULT_API_KEY,
  );
  const host =
    readNonEmpty(env.VITE_PUBLIC_POSTHOG_HOST) ?? PROJECT_POSTHOG_DEFAULT_HOST;

  if (canonical) {
    return { apiKey: canonical, host, keySource: "canonical_env" };
  }
  if (projectDefault) {
    return { apiKey: projectDefault, host, keySource: "project_default" };
  }
  return { apiKey: null, host, keySource: "none" };
}
