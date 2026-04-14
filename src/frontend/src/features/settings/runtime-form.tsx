import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  computeRuntimeUpdates,
  useRuntimeSettings,
  type RuntimeEditableKey,
  type RuntimeSecretEditableKey,
} from "./use-runtime-settings";
import type { RuntimeStatusResponse } from "@/lib/rlm-api";
import { RuntimeStatusPanel, shouldHydrateRuntimeForm, errorMessage } from "./runtime-status-panel";
import { RuntimeConnectivityPanel } from "./runtime-connectivity-panel";

export { shouldHydrateRuntimeForm, errorMessage } from "./runtime-status-panel";

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
    description: "Planner model identifier (for example: openai/gemini-3.1-pro).",
  },
  {
    key: "DSPY_LLM_API_KEY",
    label: "LM API Key",
    description: "Primary provider key for LM calls. Leave unchanged to keep current value.",
    isSecret: true,
  },
  {
    key: "DAYTONA_API_KEY",
    label: "Daytona API Key",
    description: "API Key for Daytona Workspace provisioning.",
    isSecret: true,
  },
  {
    key: "DAYTONA_API_URL",
    label: "Daytona API URL",
    description: "URL for Daytona API (e.g. http://127.0.0.1:3000).",
  },
  {
    key: "DAYTONA_TARGET",
    label: "Daytona Target",
    description: "Execution target/backend for Daytona provisioning (e.g. local).",
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
];

const RUNTIME_SECRET_KEYS: RuntimeSecretEditableKey[] = ["DSPY_LLM_API_KEY", "DAYTONA_API_KEY"];

const RUNTIME_SECRET_KEY_SET = new Set<RuntimeSecretEditableKey>(RUNTIME_SECRET_KEYS);

function isRuntimeSecretKey(key: RuntimeEditableKey): key is RuntimeSecretEditableKey {
  return RUNTIME_SECRET_KEY_SET.has(key as RuntimeSecretEditableKey);
}

export function RuntimeForm() {
  const {
    settingsQuery,
    statusQuery,
    saveSettings,
    testDaytonaConnection,
    testLmConnection,
    testAllConnections,
  } = useRuntimeSettings();

  const initialValues = settingsQuery.data?.values ?? {};
  const maskedValues = settingsQuery.data?.masked_values ?? initialValues;
  const [baselineValues, setBaselineValues] = useState<Record<string, string>>(initialValues);
  const [formValues, setFormValues] = useState<Record<string, string>>(initialValues);
  const [clearSecretFlags, setClearSecretFlags] = useState<
    Partial<Record<RuntimeSecretEditableKey, boolean>>
  >({});

  const clearedSecrets = useMemo(
    () => RUNTIME_SECRET_KEYS.filter((key) => clearSecretFlags[key] === true),
    [clearSecretFlags],
  );

  const secretInputs = useMemo(
    () =>
      Object.fromEntries(RUNTIME_SECRET_KEYS.map((key) => [key, formValues[key] ?? ""])) as Partial<
        Record<RuntimeSecretEditableKey, string>
      >,
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
  const daytonaTest = status?.tests?.daytona;
  const lmTest = status?.tests?.lm;

  useEffect(() => {
    const snapshot = settingsQuery.data;
    if (!snapshot) return;
    if (!shouldHydrateRuntimeForm(snapshot, hasUnsavedRuntimeChanges)) return;
    const nextBaseline = snapshot.values ?? {};
    const nextFormValues = { ...nextBaseline };
    for (const key of RUNTIME_SECRET_KEYS) {
      nextFormValues[key] = "";
    }
    setBaselineValues(nextBaseline);
    setFormValues(nextFormValues);
    setClearSecretFlags({});
  }, [hasUnsavedRuntimeChanges, settingsQuery.data]);

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

  const daytonaChecks = useMemo(() => {
    const source: RuntimeStatusResponse["daytona"] = statusQuery.data?.daytona ?? {};
    return Object.entries(source).filter(
      (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
    );
  }, [statusQuery.data]);

  const handleSave = () => {
    if (dirtyKeys.length === 0) {
      toast("No runtime changes to save");
      return;
    }
    saveSettings.mutate(updates, {
      onSuccess: (result) => {
        const updated = result.updated ?? [];
        if (updated.length > 0) {
          setBaselineValues((prev) => ({
            ...prev,
            ...updates,
          }));
        }
        setFormValues((prev) => {
          const next = { ...prev };
          for (const key of RUNTIME_SECRET_KEYS) {
            next[key] = "";
          }
          return next;
        });
        setClearSecretFlags({});
        toast.success("Runtime settings saved", {
          description: updated.length > 0 ? `Updated: ${updated.join(", ")}` : "No keys changed.",
        });
      },
      onError: (error) => {
        toast.error("Failed to save runtime settings", {
          description: errorMessage(error),
        });
      },
    });
  };

  const handleTestDaytona = () => {
    if (hasUnsavedRuntimeChanges) {
      showUnsavedRuntimeTestWarning();
      return;
    }

    testDaytonaConnection.mutate(undefined, {
      onSuccess: (result) => {
        toast[result.ok ? "success" : "error"]("Daytona test completed", {
          description: result.ok
            ? `Latency ${result.latency_ms ?? 0}ms`
            : result.error || "Daytona connectivity failed.",
        });
      },
      onError: (error) => {
        toast.error("Daytona test failed", {
          description: errorMessage(error),
        });
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
      if (result.lm.ok && result.daytona.ok) {
        toast.success("Runtime checks passed");
        return;
      }
      toast.error("Runtime checks reported failures", {
        description: "Review test results below.",
      });
    } catch (error) {
      toast.error("Runtime checks failed", {
        description: errorMessage(error),
      });
    }
  };

  const saveDisabled =
    !hasUnsavedRuntimeChanges || saveSettings.isPending || status?.write_enabled === false;
  const runtimeGuidance = status?.guidance ?? ["No guidance available."];

  const updateFieldValue = (key: RuntimeEditableKey, value: string) => {
    setFormValues((prev) => ({ ...prev, [key]: value }));
    if (isRuntimeSecretKey(key)) {
      setClearSecretFlags((prev) => ({
        ...prev,
        [key]: false,
      }));
    }
  };

  const toggleClearSecret = (secretKey: RuntimeSecretEditableKey) => {
    const nextClear = !(clearSecretFlags[secretKey] ?? false);
    setClearSecretFlags((prev) => ({
      ...prev,
      [secretKey]: nextClear,
    }));
    if (nextClear) {
      setFormValues((prev) => ({ ...prev, [secretKey]: "" }));
    }
  };

  return (
    <div>
      <FieldGroup className="gap-0">
        <RuntimeStatusPanel status={status} />
      </FieldGroup>

      <Card className="gap-0 rounded-xl border-border-subtle/70 shadow-none">
        <CardHeader className="border-b border-border-subtle/70">
          <CardTitle className="text-sm font-medium">Runtime Configuration</CardTitle>
          <CardDescription className="text-sm">
            Update runtime credentials, Daytona connectivity, and model selection used by the local
            backend.
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-6">
          <FieldGroup className="gap-5">
            {RUNTIME_FIELDS.map((field) => {
              const secretKey = isRuntimeSecretKey(field.key) ? field.key : null;
              const inputId = `runtime-${field.key.toLowerCase()}`;
              const inputValue = formValues[field.key] ?? "";
              return (
                <Field key={field.key}>
                  <FieldLabel htmlFor={inputId}>{field.label}</FieldLabel>
                  {field.isSecret && secretKey ? (
                    <InputGroup className="max-w-xl">
                      <InputGroupInput
                        id={inputId}
                        type="password"
                        value={inputValue}
                        placeholder={field.placeholder}
                        autoComplete="off"
                        aria-label={field.label}
                        onChange={(event) => updateFieldValue(field.key, event.currentTarget.value)}
                      />
                      <InputGroupAddon align="inline-end">
                        <InputGroupButton
                          type="button"
                          size="sm"
                          variant={clearSecretFlags[secretKey] ? "secondary" : "outline"}
                          className="h-full rounded-none border-y-0 border-r-0 border-l border-border-subtle/70 px-4 shadow-none"
                          aria-pressed={clearSecretFlags[secretKey] ?? false}
                          onClick={() => toggleClearSecret(secretKey)}
                        >
                          {clearSecretFlags[secretKey] ? "Will clear on save" : "Clear saved value"}
                        </InputGroupButton>
                      </InputGroupAddon>
                    </InputGroup>
                  ) : (
                    <Input
                      id={inputId}
                      type="text"
                      value={inputValue}
                      placeholder={field.placeholder}
                      autoComplete="off"
                      aria-label={field.label}
                      onChange={(event) => updateFieldValue(field.key, event.currentTarget.value)}
                      className="max-w-xl"
                    />
                  )}
                  <FieldDescription>{field.description}</FieldDescription>
                  {field.isSecret && secretKey ? (
                    <FieldDescription>
                      Write-only input. Configured value:{" "}
                      {maskedValues[secretKey] ? maskedValues[secretKey] : "not set"}.
                    </FieldDescription>
                  ) : null}
                </Field>
              );
            })}
          </FieldGroup>
        </CardContent>
        <CardFooter className="border-t border-border-subtle/70 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            Writes to <code>.env</code> (local only), updates process env, and refreshes the active
            runtime configuration.
          </p>
          <Button
            variant="secondary"
            className="rounded-lg"
            onClick={handleSave}
            disabled={saveDisabled}
          >
            {saveSettings.isPending ? "Saving…" : "Save settings"}
          </Button>
        </CardFooter>
      </Card>

      <RuntimeConnectivityPanel
        hasUnsavedRuntimeChanges={hasUnsavedRuntimeChanges}
        writeEnabled={status?.write_enabled !== false}
        daytonaTest={daytonaTest}
        lmTest={lmTest}
        llmChecks={llmChecks}
        daytonaChecks={daytonaChecks}
        runtimeGuidance={runtimeGuidance}
        onTestLm={handleTestLm}
        onTestDaytona={handleTestDaytona}
        onTestAll={handleTestAll}
        testLmPending={testLmConnection.isPending}
        testDaytonaPending={testDaytonaConnection.isPending}
      />
    </div>
  );
}
