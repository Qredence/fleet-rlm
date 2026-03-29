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
  SECRET_NAME: "LITELLM",
  VOLUME_NAME: "rlm-volume-dspy",
};

const fallbackMaskedValues: Record<string, string> = {
  DSPY_LLM_API_KEY: "sk-...demo",
  MODAL_TOKEN_ID: "tok-...demo",
  MODAL_TOKEN_SECRET: "***",
  ...fallbackValues,
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function buildConnectivityTest(
  kind: "modal" | "lm" | "daytona",
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
    latency_ms: kind === "modal" ? 82 : 126,
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
    llm: {
      model_set: true,
      api_key_set: true,
      planner_configured: true,
    },
    modal: {
      credentials_available: true,
      secret_name_set: true,
      volume_name_set: true,
    },
    tests: {
      modal: buildConnectivityTest("modal"),
      lm: buildConnectivityTest("lm"),
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
    fallbackMaskedValues[key] =
      key.endsWith("API_KEY") ||
      key.endsWith("TOKEN_ID") ||
      key.endsWith("TOKEN_SECRET")
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

export function getMockModalTest(): RuntimeConnectivityTestResponse {
  return buildConnectivityTest("modal");
}

export function getMockLmTest(): RuntimeConnectivityTestResponse {
  return buildConnectivityTest("lm");
}

export function getMockDaytonaTest(): RuntimeConnectivityTestResponse {
  return buildConnectivityTest("daytona");
}
