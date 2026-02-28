import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsRow } from "@/components/shared/SettingsRow";
import {
  computeRuntimeUpdates,
  useRuntimeSettings,
  type RuntimeEditableKey,
  type RuntimeSecretEditableKey,
} from "@/features/settings/useRuntimeSettings";
import type { RuntimeConnectivityTestResponse } from "@/lib/rlm-api";

type RuntimeField = {
  key: RuntimeEditableKey;
  label: string;
  description: string;
  isSecret?: boolean;
  placeholder?: string;
};

const RUNTIME_FIELDS: RuntimeField[] = [
  {
    key: "DSPY_LM_MODEL",
    label: "LM Model",
    description: "Planner model identifier (for example: openai/gpt-4o-mini).",
  },
  {
    key: "DSPY_LLM_API_KEY",
    label: "LM API Key",
    description:
      "Primary provider key for LM calls. Leave unchanged to keep current value.",
    isSecret: true,
  },
  {
    key: "DSPY_LM_API_BASE",
    label: "LM API Base",
    description: "Optional base URL for LM provider routing.",
  },
  {
    key: "DSPY_LM_MAX_TOKENS",
    label: "LM Max Tokens",
    description: "Maximum token budget per planner response.",
    placeholder: "64000",
  },
  {
    key: "MODAL_TOKEN_ID",
    label: "Modal Token ID",
    description:
      "Optional Modal token ID override. Leave unchanged to keep current value.",
    isSecret: true,
  },
  {
    key: "MODAL_TOKEN_SECRET",
    label: "Modal Token Secret",
    description:
      "Optional Modal token secret override. Leave unchanged to keep current value.",
    isSecret: true,
  },
  {
    key: "SECRET_NAME",
    label: "Modal Secret Name",
    description: "Modal secret mounted into sandbox sessions.",
    placeholder: "LITELLM",
  },
  {
    key: "VOLUME_NAME",
    label: "Modal Volume Name",
    description: "Modal volume mounted at /data for runtime state.",
    placeholder: "rlm-volume-dspy",
  },
];

const RUNTIME_SECRET_KEYS: RuntimeSecretEditableKey[] = [
  "DSPY_LLM_API_KEY",
  "MODAL_TOKEN_ID",
  "MODAL_TOKEN_SECRET",
];

const RUNTIME_SECRET_KEY_SET = new Set<RuntimeSecretEditableKey>(
  RUNTIME_SECRET_KEYS,
);

function isRuntimeSecretKey(key: RuntimeEditableKey): key is RuntimeSecretEditableKey {
  return RUNTIME_SECRET_KEY_SET.has(key as RuntimeSecretEditableKey);
}

function formatCheckLabel(key: string): string {
  return key
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function testSummary(test: RuntimeConnectivityTestResponse | null | undefined) {
  if (!test) return "Not run yet";
  if (test.ok) return "Pass";
  if (!test.preflight_ok) return "Preflight failed";
  return "Failed";
}

function testVariant(test: RuntimeConnectivityTestResponse | null | undefined) {
  if (!test) return "outline" as const;
  return test.ok ? ("success" as const) : ("destructive-subtle" as const);
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

export function RuntimePane() {
  const {
    settingsQuery,
    statusQuery,
    saveSettings,
    testModalConnection,
    testLmConnection,
    testAllConnections,
  } = useRuntimeSettings();

  const initialValues = settingsQuery.data?.values ?? {};
  const maskedValues = settingsQuery.data?.masked_values ?? initialValues;
  const [baselineValues, setBaselineValues] =
    useState<Record<string, string>>(initialValues);
  const [formValues, setFormValues] =
    useState<Record<string, string>>(initialValues);
  const [clearSecretFlags, setClearSecretFlags] = useState<
    Partial<Record<RuntimeSecretEditableKey, boolean>>
  >({});

  useEffect(() => {
    const snapshot = settingsQuery.data;
    if (!snapshot) return;
    const nextBaseline = snapshot.values ?? {};
    const nextFormValues = { ...nextBaseline };
    for (const key of RUNTIME_SECRET_KEYS) {
      nextFormValues[key] = "";
    }
    setBaselineValues(nextBaseline);
    setFormValues(nextFormValues);
    setClearSecretFlags({});
  }, [settingsQuery.data]);

  const clearedSecrets = useMemo(
    () =>
      RUNTIME_SECRET_KEYS.filter((key) => clearSecretFlags[key] === true),
    [clearSecretFlags],
  );

  const secretInputs = useMemo(
    () =>
      Object.fromEntries(
        RUNTIME_SECRET_KEYS.map((key) => [key, formValues[key] ?? ""]),
      ) as Partial<Record<RuntimeSecretEditableKey, string>>,
    [formValues],
  );

  const updates = useMemo(
    () =>
      computeRuntimeUpdates(formValues, baselineValues, {
        secretInputs,
        clearedSecrets,
      }),
    [baselineValues, clearedSecrets, formValues, secretInputs],
  );
  const dirtyKeys = useMemo(() => Object.keys(updates), [updates]);
  const hasUnsavedRuntimeChanges = dirtyKeys.length > 0;
  const status = statusQuery.data;
  const modalTest = status?.tests?.modal;
  const lmTest = status?.tests?.lm;
  const activeModels = status?.active_models;

  const showUnsavedRuntimeTestWarning = () => {
    toast.error("Save runtime settings before testing", {
      description:
        status?.write_enabled === false
          ? "Runtime writes are disabled outside local mode, so tests use currently active values only."
          : "Connection tests validate active runtime credentials, not unsaved form edits.",
    });
  };

  const llmChecks = useMemo(() => {
    const source = statusQuery.data?.llm ?? {};
    return Object.entries(source).filter(
      (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
    );
  }, [statusQuery.data?.llm]);

  const modalChecks = useMemo(() => {
    const source = statusQuery.data?.modal ?? {};
    return Object.entries(source).filter(
      (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
    );
  }, [statusQuery.data?.modal]);

  const handleSave = () => {
    if (dirtyKeys.length === 0) {
      toast("No runtime changes to save");
      return;
    }
    saveSettings.mutate(updates, {
      onSuccess: (result) => {
        const updated = result.updated ?? [];
        toast.success("Runtime settings saved", {
          description:
            updated.length > 0
              ? `Updated: ${updated.join(", ")}`
              : "No keys changed.",
        });
      },
      onError: (error) => {
        toast.error("Failed to save runtime settings", {
          description: errorMessage(error),
        });
      },
    });
  };

  const handleTestModal = () => {
    if (hasUnsavedRuntimeChanges) {
      showUnsavedRuntimeTestWarning();
      return;
    }

    testModalConnection.mutate(undefined, {
      onSuccess: (result) => {
        toast[result.ok ? "success" : "error"]("Modal test completed", {
          description: result.ok
            ? `Latency ${result.latency_ms ?? 0}ms`
            : result.error || "Modal connectivity failed.",
        });
      },
      onError: (error) => {
        toast.error("Modal test failed", { description: errorMessage(error) });
      },
    });
  };

  const handleTestLm = () => {
    if (hasUnsavedRuntimeChanges) {
      showUnsavedRuntimeTestWarning();
      return;
    }

    testLmConnection.mutate(undefined, {
      onSuccess: (result) => {
        toast[result.ok ? "success" : "error"]("LM test completed", {
          description: result.ok
            ? `Latency ${result.latency_ms ?? 0}ms`
            : result.error || "LM connectivity failed.",
        });
      },
      onError: (error) => {
        toast.error("LM test failed", { description: errorMessage(error) });
      },
    });
  };

  const handleTestAll = async () => {
    if (hasUnsavedRuntimeChanges) {
      showUnsavedRuntimeTestWarning();
      return;
    }

    try {
      const result = await testAllConnections();
      if (result.modal.ok && result.lm.ok) {
        toast.success("Runtime checks passed");
        return;
      }
      toast.error("Runtime checks reported failures", {
        description: "Review Modal/LM test results below.",
      });
    } catch (error) {
      toast.error("Runtime checks failed", {
        description: errorMessage(error),
      });
    }
  };

  const saveDisabled =
    !hasUnsavedRuntimeChanges ||
    saveSettings.isPending ||
    status?.write_enabled === false;

  return (
    <div>
      <SettingsRow
        label="Runtime Status"
        description={
          status
            ? `Environment: ${status.app_env}. Runtime readiness is ${
                status.ready ? "healthy" : "degraded"
              }.`
            : "Loading runtime status…"
        }
      >
        <Badge variant={status?.ready ? "success" : "warning"}>
          {status?.ready ? "Ready" : "Needs Attention"}
        </Badge>
      </SettingsRow>

      <SettingsRow
        label="Active Models"
        description="Resolved runtime model identifiers currently used for planner/delegate execution."
      >
        <div className="text-xs text-muted-foreground text-right">
          <div>Planner: {activeModels?.planner || "not set"}</div>
          <div>Delegate: {activeModels?.delegate || "not set"}</div>
          <div>Delegate small: {activeModels?.delegate_small || "not set"}</div>
        </div>
      </SettingsRow>

      {status?.write_enabled === false && (
        <SettingsRow
          label="Write Protection"
          description="Runtime settings updates are disabled because APP_ENV is not local."
        >
          <Badge variant="destructive-subtle">Read-only</Badge>
        </SettingsRow>
      )}

      <div className="py-3 border-b border-border-subtle">
        <span className="text-sm text-muted-foreground font-medium">
          Runtime Configuration
        </span>
      </div>

      {RUNTIME_FIELDS.map((field) => {
        const secretKey = isRuntimeSecretKey(field.key) ? field.key : null;
        return (
          <SettingsRow
            key={field.key}
            label={field.label}
            description={field.description}
          >
            <div className="flex flex-col items-start gap-2">
              <Input
                type={field.isSecret ? "password" : "text"}
                value={formValues[field.key] ?? ""}
                placeholder={field.placeholder}
                autoComplete="off"
                onChange={(event) => {
                  const value = event.target.value;
                  setFormValues((prev) => ({ ...prev, [field.key]: value }));
                  if (field.isSecret && secretKey) {
                    setClearSecretFlags((prev) => ({
                      ...prev,
                      [secretKey]: false,
                    }));
                  }
                }}
                className="w-[260px] max-w-[50vw]"
              />
              {field.isSecret && secretKey && (
                <>
                  <p className="text-xs text-muted-foreground">
                    Write-only input. Configured value:{" "}
                    {maskedValues[secretKey] ? maskedValues[secretKey] : "not set"}.
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant={clearSecretFlags[secretKey] ? "secondary" : "outline"}
                    className="rounded-lg"
                    onClick={() => {
                      const nextClear = !(clearSecretFlags[secretKey] ?? false);
                      setClearSecretFlags((prev) => ({
                        ...prev,
                        [secretKey]: nextClear,
                      }));
                      if (nextClear) {
                        setFormValues((prev) => ({ ...prev, [secretKey]: "" }));
                      }
                    }}
                  >
                    {clearSecretFlags[secretKey]
                      ? "Will clear on save"
                      : "Clear saved value"}
                  </Button>
                </>
              )}
            </div>
          </SettingsRow>
        );
      })}

      <SettingsRow
        label="Save Runtime Settings"
        description="Writes to .env (local only), updates process env, and refreshes in-memory runtime."
      >
        <Button
          variant="secondary"
          className="rounded-lg"
          onClick={handleSave}
          disabled={saveDisabled}
        >
          {saveSettings.isPending ? "Saving…" : "Save settings"}
        </Button>
      </SettingsRow>

      <SettingsRow
        label="Test Credentials + Connection"
        description="Runs preflight credential checks plus live Modal + LM connectivity smoke tests."
      >
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            className="rounded-lg"
            onClick={handleTestModal}
            disabled={testModalConnection.isPending}
          >
            {testModalConnection.isPending
              ? "Testing Modal…"
              : "Test Modal Connection"}
          </Button>
          <Button
            variant="outline"
            className="rounded-lg"
            onClick={handleTestLm}
            disabled={testLmConnection.isPending}
          >
            {testLmConnection.isPending ? "Testing LM…" : "Test LM Connection"}
          </Button>
          <Button
            variant="secondary"
            className="rounded-lg"
            onClick={handleTestAll}
            disabled={
              testModalConnection.isPending || testLmConnection.isPending
            }
          >
            Test Credentials + Connection
          </Button>
        </div>
        {hasUnsavedRuntimeChanges && (
          <p className="text-xs text-muted-foreground mt-2">
            Save runtime settings first so tests run against your latest
            credentials and provider configuration.
          </p>
        )}
      </SettingsRow>

      <SettingsRow
        label="Modal Smoke"
        description={`Last result: ${testSummary(modalTest)}`}
      >
        <Badge variant={testVariant(modalTest)}>
          {modalTest?.checked_at
            ? `${testSummary(modalTest)} • ${new Date(
                modalTest.checked_at,
              ).toLocaleString()}`
            : "Not run"}
        </Badge>
      </SettingsRow>

      <SettingsRow
        label="LM Smoke"
        description={`Last result: ${testSummary(lmTest)}`}
      >
        <Badge variant={testVariant(lmTest)}>
          {lmTest?.checked_at
            ? `${testSummary(lmTest)} • ${new Date(lmTest.checked_at).toLocaleString()}`
            : "Not run"}
        </Badge>
      </SettingsRow>

      <div className="py-4 border-b border-border-subtle">
        <p className="text-sm text-foreground font-medium mb-2">
          Preflight Checks
        </p>
        <div className="flex flex-wrap gap-2">
          {llmChecks.map(([key, ok]) => (
            <Badge
              key={`llm-${key}`}
              variant={ok ? "success" : "destructive-subtle"}
            >
              LM {formatCheckLabel(key)}: {ok ? "configured" : "missing"}
            </Badge>
          ))}
          {modalChecks.map(([key, ok]) => (
            <Badge
              key={`modal-${key}`}
              variant={ok ? "success" : "destructive-subtle"}
            >
              Modal {formatCheckLabel(key)}: {ok ? "configured" : "missing"}
            </Badge>
          ))}
        </div>
      </div>

      <div className="py-4">
        <p className="text-sm text-foreground font-medium mb-2">Guidance</p>
        <ul className="list-disc pl-5 space-y-1 text-sm text-muted-foreground">
          {(status?.guidance ?? ["No guidance available."]).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
