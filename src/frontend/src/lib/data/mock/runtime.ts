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
  DSPY_DELEGATE_LM_MAX_TOKENS: "64000",
  DSPY_LM_API_BASE: "",
  DSPY_LM_MAX_TOKENS: "64000",
  DSPY_ADAPTER: "",
  DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING: "false",
  DAYTONA_API_URL: "http://127.0.0.1:3000",
  DAYTONA_TARGET: "local",
  VOLUME_NAME: "rlm-volume-dspy",
  TIMEOUT: "900",
  INTERPRETER_ASYNC_EXECUTE: "true",
  DATABASE_REQUIRED: "false",
  DB_ECHO: "false",
  DB_VALIDATE_ON_STARTUP: "false",
};

const fallbackMaskedValues: Record<string, string> = {
  DSPY_LLM_API_KEY: "sk-...demo",
  DSPY_LM_API_KEY: "sk-...alt",
  DSPY_DELEGATE_LM_API_KEY: "sk-...delegate",
  DAYTONA_API_KEY: "dyt-...demo",
  POSTHOG_API_KEY: "phc-...demo",
  DATABASE_URL: "pos.../db",
  DATABASE_ADMIN_URL: "pos...admin",
  ...fallbackValues,
};

const runtimeSettingCategories = [
  {
    id: "llm",
    label: "LLM provider and models",
    description: "Planner, delegate, adapter, and provider routing settings used by DSPy.",
    fields: [
      ["DSPY_LM_MODEL", "Planner LM model", "Model identifier for the planner runtime."],
      [
        "DSPY_DELEGATE_LM_MODEL",
        "Delegate LM model",
        "Optional model identifier for recursive delegate turns.",
      ],
      [
        "DSPY_DELEGATE_LM_SMALL_MODEL",
        "Delegate small LM model",
        "Optional small model used by lightweight delegate tasks.",
      ],
      [
        "DSPY_DELEGATE_LM_MAX_TOKENS",
        "Delegate max tokens",
        "Maximum output tokens per delegate model response.",
      ],
      [
        "DSPY_LM_API_BASE",
        "Provider API base",
        "Optional custom API base URL for LiteLLM-compatible providers.",
      ],
      ["DSPY_LM_MAX_TOKENS", "Planner max tokens", "Maximum output tokens per planner response."],
      [
        "DSPY_ADAPTER",
        "DSPy adapter",
        "Optional default DSPy adapter for non-runtime-module calls.",
      ],
      [
        "DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING",
        "Native function calling",
        "Enable native function calling for the default DSPy adapter.",
      ],
    ],
  },
  {
    id: "api_keys",
    label: "API keys and credentials",
    description:
      "Write-only credentials used by language-model providers, Daytona, and optional services.",
    fields: [
      [
        "DSPY_LLM_API_KEY",
        "Primary LM API key",
        "Primary provider key for planner and fallback delegate LM calls.",
      ],
      [
        "DSPY_LM_API_KEY",
        "Legacy LM API key",
        "Backward-compatible LM provider key used when the primary key is unset.",
      ],
      [
        "DSPY_DELEGATE_LM_API_KEY",
        "Delegate LM API key",
        "Optional provider key dedicated to delegate model calls.",
      ],
      [
        "DAYTONA_API_KEY",
        "Daytona API key",
        "API key used for Daytona workspace and volume provisioning.",
      ],
      ["POSTHOG_API_KEY", "PostHog API key", "Optional PostHog project key for analytics."],
    ],
  },
  {
    id: "sandbox_volumes",
    label: "Sandbox and volumes",
    description: "Daytona runtime, sandbox execution, and durable volume parameters.",
    fields: [
      ["DAYTONA_API_URL", "Daytona API URL", "Base URL for the Daytona API."],
      [
        "DAYTONA_TARGET",
        "Daytona target",
        "Execution target or backend selected for Daytona provisioning.",
      ],
      ["VOLUME_NAME", "Volume name", "Durable Daytona volume mounted into workbench sandboxes."],
      ["TIMEOUT", "Sandbox timeout", "Maximum sandbox execution time in seconds."],
      [
        "INTERPRETER_ASYNC_EXECUTE",
        "Async interpreter execution",
        "Run interpreter execute calls through the async wrapper.",
      ],
    ],
  },
  {
    id: "database",
    label: "Database",
    description: "Postgres persistence URLs and database startup behavior.",
    fields: [
      [
        "DATABASE_URL",
        "Runtime database URL",
        "Pooled Postgres URL used by application runtime traffic.",
      ],
      [
        "DATABASE_ADMIN_URL",
        "Admin database URL",
        "Direct Postgres URL used for Alembic, schema, and admin tasks.",
      ],
      [
        "DATABASE_REQUIRED",
        "Require database",
        "Require database connectivity during server startup.",
      ],
      ["DB_ECHO", "SQL echo", "Enable SQLAlchemy SQL echo logging."],
      [
        "DB_VALIDATE_ON_STARTUP",
        "Validate database on startup",
        "Ping the configured database during server startup.",
      ],
    ],
  },
] as const;

function isMockSecretKey(key: string): boolean {
  return key.endsWith("API_KEY") || (key.endsWith("_URL") && key.startsWith("DATABASE_"));
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
    categories: runtimeSettingCategories.map((category) => ({
      id: category.id,
      label: category.label,
      description: category.description,
      fields: category.fields.map(([key, label, description]) => ({
        key,
        label,
        description,
        value: fallbackMaskedValues[key] ?? fallbackValues[key] ?? "",
        masked_value: fallbackMaskedValues[key] ?? fallbackValues[key] ?? "",
        secret: isMockSecretKey(key),
        editable: true,
        reload_required: key.startsWith("DSPY_"),
        placeholder: null,
        default: null,
      })),
    })),
  } as RuntimeSettingsSnapshot;
}

export function getMockRuntimeStatus(): RuntimeStatusResponse {
  return {
    app_env: "local",
    write_enabled: true,
    settings_write_enabled: true,
    profile_write_enabled: true,
    ready: true,
    execution_backend: "legacy_agent_runtime",
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
    fallbackMaskedValues[key] = isMockSecretKey(key)
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
