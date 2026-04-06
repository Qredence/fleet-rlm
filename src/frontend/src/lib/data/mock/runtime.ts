import type {
  RuntimeConnectivityTestResponse,
  RuntimeSettingsSnapshot,
  RuntimeSettingsUpdateResponse,
  RuntimeStatusResponse,
} from "@/lib/rlm-api/types";

const FALLBACK_ENV_PATH = ".env";

const fallbackValues: Record<string, string> = {
  DSPY_LM_MODEL: "openai/gemini-3-flash-preview",
  DSPY_DELEGATE_LM_MODEL: "openai/gemini-3-flash-preview",
  DSPY_DELEGATE_LM_SMALL_MODEL: "openai/gemini-3-flash-preview",
  DSPY_LM_API_BASE: "",
  DSPY_LM_MAX_TOKENS: "64000",
  DAYTONA_API_URL: "http://127.0.0.1:3000",
  DAYTONA_TARGET: "local",
};

const fallbackMaskedValues: Record<string, string> = {
  DSPY_LLM_API_KEY: "sk-...demo",
  DAYTONA_API_KEY: "dyt-...demo",
  ...fallbackValues,
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function buildConnectivityTest(
  kind: "lm" | "daytona",
  overrides?: Partial<RuntimeConnectivityTestResponse>,
): RuntimeConnectivityTestResponse {
  return {
    kind,
    ok: true,
    preflight_ok: true,
    checked_at: new Date().toISOString(),
    checks: {
      credentials_available: true,
      provider_reachable: true,
    },
    guidance: [],
    latency_ms: kind === "daytona" ? 82 : 126,
    output_preview: kind === "lm" ? "Mock runtime ready." : null,
    error: null,
    ...overrides,
  };
}

export function getMockRuntimeSettings(): RuntimeSettingsSnapshot {
  return {
    env_path: FALLBACK_ENV_PATH,
    keys: Object.keys(fallbackValues),
    values: clone(fallbackValues),
    masked_values: clone(fallbackMaskedValues),
  };
}

export function getMockRuntimeStatus(): RuntimeStatusResponse {
  return {
    app_env: "local",
    write_enabled: true,
    ready: true,
    active_models: {
      planner: fallbackValues.DSPY_LM_MODEL,
      delegate: fallbackValues.DSPY_DELEGATE_LM_MODEL,
      delegate_small: fallbackValues.DSPY_DELEGATE_LM_SMALL_MODEL,
    },
    sandbox_provider: "daytona",
    llm: {
      model_set: true,
      api_key_set: true,
      planner_configured: true,
    },
    daytona: {
      api_key_set: true,
      api_url_set: true,
      target_set: true,
      configured: true,
      sandbox_provider_set: true,
    },
    tests: {
      lm: buildConnectivityTest("lm"),
      daytona: buildConnectivityTest("daytona"),
    },
    guidance: [
      "Frontend dev mode is using built-in runtime fallback data because no backend runtime is configured.",
    ],
  };
}

export function applyMockRuntimeUpdates(
  updates: Record<string, string>,
): RuntimeSettingsUpdateResponse {
  for (const [key, value] of Object.entries(updates)) {
    if (value === "") {
      delete fallbackValues[key];
      delete fallbackMaskedValues[key];
      continue;
    }

    fallbackValues[key] = value;
    fallbackMaskedValues[key] = key.endsWith("API_KEY")
      ? value.length > 8
        ? `${value.slice(0, 2)}...${value.slice(-4)}`
        : "***"
      : value;
  }

  return {
    updated: Object.keys(updates),
    env_path: FALLBACK_ENV_PATH,
  };
}

export function getMockLmTest(): RuntimeConnectivityTestResponse {
  return buildConnectivityTest("lm");
}

export function getMockDaytonaTest(): RuntimeConnectivityTestResponse {
  return buildConnectivityTest("daytona");
}
