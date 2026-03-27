import { useEffect, useMemo, useState } from "react";
import { AlertCircleIcon, BadgeCheckIcon, Clock3Icon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field";
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
} from "@/screens/settings/use-runtime-settings";
import type {
  RuntimeConnectivityTestResponse,
  RuntimeStatusResponse,
} from "@/lib/rlm-api";

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
    description:
      "Planner model identifier (for example: openai/gemini-3.1-pro).",
  },
  {
    key: "DSPY_LLM_API_KEY",
    label: "LM API Key",
    description:
      "Primary provider key for LM calls. Leave unchanged to keep current value.",
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
    description: "Target workspace provider for Daytona (e.g. local).",
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
    key: "SANDBOX_PROVIDER",
    label: "Sandbox Provider",
    description: "Primary sandbox and volume backend (modal or daytona).",
    placeholder: "modal",
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

function isRuntimeSecretKey(
  key: RuntimeEditableKey,
): key is RuntimeSecretEditableKey {
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
  return test.ok ? ("default" as const) : ("destructive" as const);
}

function testLabel(test: RuntimeConnectivityTestResponse | null | undefined) {
  if (!test) return "Not run";
  return testSummary(test);
}

function formatCheckedAt(checkedAt: string | null | undefined) {
  if (!checkedAt) return null;
  return new Date(checkedAt).toLocaleString();
}

const SETTINGS_FIELD_CLASSNAME =
  "border-b border-border-subtle py-4 last:border-b-0";

export function RuntimeForm() {
  const {
    settingsQuery,
    statusQuery,
    saveSettings,
    testModalConnection,
    testDaytonaConnection,
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

  const clearedSecrets = useMemo(
    () => RUNTIME_SECRET_KEYS.filter((key) => clearSecretFlags[key] === true),
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
  const daytonaTest = status?.tests?.daytona;
  const lmTest = status?.tests?.lm;
  const activeModels = status?.active_models;

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

  const modalChecks = useMemo(() => {
    const source = statusQuery.data?.modal ?? {};
    return Object.entries(source).filter(
      (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
    );
  }, [statusQuery.data?.modal]);

  const daytonaChecks = useMemo(() => {
    const source: RuntimeStatusResponse["daytona"] =
      statusQuery.data?.daytona ?? {};
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
      if (result.modal.ok && result.lm.ok && (result.daytona?.ok ?? true)) {
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
    !hasUnsavedRuntimeChanges ||
    saveSettings.isPending ||
    status?.write_enabled === false;
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
        <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Runtime Status</FieldTitle>
            <FieldDescription>
              {status
                ? `Environment: ${status.app_env}. Runtime readiness is ${
                    status.ready ? "healthy" : "degraded"
                  }.`
                : "Loading runtime status…"}
            </FieldDescription>
          </FieldContent>
          <Badge variant={status?.ready ? "default" : "secondary"}>
            {status?.ready ? "Ready" : "Needs Attention"}
          </Badge>
        </Field>

        <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Active Models</FieldTitle>
            <FieldDescription>
              Resolved runtime model identifiers currently used for
              planner/delegate execution.
            </FieldDescription>
          </FieldContent>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right text-xs text-muted-foreground">
            <div>Planner: {activeModels?.planner || "not set"}</div>
            <div>Delegate: {activeModels?.delegate || "not set"}</div>
            <div>
              Delegate small: {activeModels?.delegate_small || "not set"}
            </div>
          </div>
        </Field>

        {status?.write_enabled === false ? (
          <Field orientation="responsive" className="py-4">
            <FieldContent>
              <FieldTitle>Write Protection</FieldTitle>
              <FieldDescription>
                Runtime settings updates are disabled because APP_ENV is not
                local.
              </FieldDescription>
            </FieldContent>
            <Badge variant="destructive">Read-only</Badge>
          </Field>
        ) : null}
      </FieldGroup>

      <Card className="gap-0 rounded-xl border-border-subtle/70 shadow-none">
        <CardHeader className="border-b border-border-subtle/70">
          <CardTitle className="text-sm font-medium">
            Runtime Configuration
          </CardTitle>
          <CardDescription className="text-sm">
            Update runtime credentials, model selection, and Modal resource
            names used by the local environment.
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-6">
          <FieldGroup className="gap-5">
            {RUNTIME_FIELDS.map((field) => {
              const secretKey = isRuntimeSecretKey(field.key)
                ? field.key
                : null;
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
                        onChange={(event) =>
                          updateFieldValue(field.key, event.currentTarget.value)
                        }
                      />
                      <InputGroupAddon align="inline-end">
                        <InputGroupButton
                          type="button"
                          size="sm"
                          variant={
                            clearSecretFlags[secretKey]
                              ? "secondary"
                              : "outline"
                          }
                          className="h-full rounded-none border-y-0 border-r-0 border-l border-border-subtle/70 px-4 shadow-none"
                          aria-pressed={clearSecretFlags[secretKey] ?? false}
                          onClick={() => toggleClearSecret(secretKey)}
                        >
                          {clearSecretFlags[secretKey]
                            ? "Will clear on save"
                            : "Clear saved value"}
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
                      onChange={(event) =>
                        updateFieldValue(field.key, event.currentTarget.value)
                      }
                      className="max-w-xl"
                    />
                  )}
                  <FieldDescription>{field.description}</FieldDescription>
                  {field.isSecret && secretKey ? (
                    <FieldDescription>
                      Write-only input. Configured value:{" "}
                      {maskedValues[secretKey]
                        ? maskedValues[secretKey]
                        : "not set"}
                      .
                    </FieldDescription>
                  ) : null}
                </Field>
              );
            })}
          </FieldGroup>
        </CardContent>
        <CardFooter className="border-t border-border-subtle/70 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            Writes to <code>.env</code> (local only), updates process env, and
            refreshes the active runtime configuration.
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

      <Card className="mt-4 gap-0 rounded-xl border-border-subtle/70 shadow-none">
        <CardHeader className="border-b border-border-subtle/70">
          <CardTitle className="text-sm font-medium">
            Test Credentials + Connection
          </CardTitle>
          <CardDescription className="max-w-xl text-sm">
            Runs preflight credential checks plus live Modal and LM connectivity
            smoke tests.
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-6">
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2.5">
              <Button
                variant="secondary"
                size="lg"
                className="rounded-lg"
                onClick={handleTestModal}
                disabled={testModalConnection.isPending}
              >
                {testModalConnection.isPending
                  ? "Testing Modal…"
                  : "Test Modal"}
              </Button>
              <Button
                variant="outline"
                size="lg"
                className="rounded-lg"
                onClick={handleTestLm}
                disabled={testLmConnection.isPending}
              >
                {testLmConnection.isPending ? "Testing LM…" : "Test LM"}
              </Button>
              <Button
                variant="outline"
                size="lg"
                className="rounded-lg"
                onClick={handleTestDaytona}
                disabled={testDaytonaConnection.isPending}
              >
                {testDaytonaConnection.isPending
                  ? "Testing Daytona…"
                  : "Test Daytona"}
              </Button>
              <Button
                variant="secondary"
                size="lg"
                className="rounded-lg"
                onClick={handleTestAll}
                disabled={
                  testModalConnection.isPending ||
                  testLmConnection.isPending ||
                  testDaytonaConnection.isPending
                }
              >
                Test All Connections
              </Button>
            </div>
            {hasUnsavedRuntimeChanges ? (
              <p className="text-xs leading-5 text-muted-foreground">
                Save runtime settings first so tests run against your latest
                credentials and provider configuration.
              </p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <FieldGroup className="gap-0">
        <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Modal Smoke</FieldTitle>
            <FieldDescription>{`Last result: ${testSummary(modalTest)}`}</FieldDescription>
          </FieldContent>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right">
            <Badge variant={testVariant(modalTest)}>
              {modalTest?.checked_at ? (
                modalTest.ok ? (
                  <BadgeCheckIcon />
                ) : (
                  <AlertCircleIcon />
                )
              ) : (
                <Clock3Icon />
              )}
              {testLabel(modalTest)}
            </Badge>
            {modalTest?.checked_at ? (
              <span className="text-xs text-muted-foreground">
                {formatCheckedAt(modalTest.checked_at)}
              </span>
            ) : null}
          </div>
        </Field>

        <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Daytona Smoke</FieldTitle>
            <FieldDescription>{`Last result: ${testSummary(daytonaTest)}`}</FieldDescription>
          </FieldContent>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right">
            <Badge variant={testVariant(daytonaTest)}>
              {daytonaTest?.checked_at ? (
                daytonaTest.ok ? (
                  <BadgeCheckIcon />
                ) : (
                  <AlertCircleIcon />
                )
              ) : (
                <Clock3Icon />
              )}
              {testLabel(daytonaTest)}
            </Badge>
            {daytonaTest?.checked_at ? (
              <span className="text-xs text-muted-foreground">
                {formatCheckedAt(daytonaTest.checked_at)}
              </span>
            ) : null}
          </div>
        </Field>

        <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>LM Smoke</FieldTitle>
            <FieldDescription>{`Last result: ${testSummary(lmTest)}`}</FieldDescription>
          </FieldContent>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right">
            <Badge variant={testVariant(lmTest)}>
              {lmTest?.checked_at ? (
                lmTest.ok ? (
                  <BadgeCheckIcon />
                ) : (
                  <AlertCircleIcon />
                )
              ) : (
                <Clock3Icon />
              )}
              {testLabel(lmTest)}
            </Badge>
            {lmTest?.checked_at ? (
              <span className="text-xs text-muted-foreground">
                {formatCheckedAt(lmTest.checked_at)}
              </span>
            ) : null}
          </div>
        </Field>

        <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Preflight Checks</FieldTitle>
            <FieldDescription>
              Credential and provider availability.
            </FieldDescription>
          </FieldContent>
          <div className="flex max-w-xl flex-wrap justify-end gap-2">
            {llmChecks.map(([key, ok]) => (
              <Badge
                key={`llm-${key}`}
                variant={ok ? "outline" : "destructive"}
                className={
                  ok
                    ? "border-chart-3/30 bg-chart-3/10 text-chart-3"
                    : undefined
                }
              >
                {ok ? <BadgeCheckIcon /> : <AlertCircleIcon />}
                LM {formatCheckLabel(key)}
              </Badge>
            ))}
            {modalChecks.map(([key, ok]) => (
              <Badge
                key={`modal-${key}`}
                variant={ok ? "outline" : "destructive"}
                className={
                  ok
                    ? "border-chart-3/30 bg-chart-3/10 text-chart-3"
                    : undefined
                }
              >
                {ok ? <BadgeCheckIcon /> : <AlertCircleIcon />}
                Modal {formatCheckLabel(key)}
              </Badge>
            ))}
            {daytonaChecks.map(([key, ok]) => (
              <Badge
                key={`daytona-${key}`}
                variant={ok ? "outline" : "destructive"}
                className={
                  ok
                    ? "border-chart-3/30 bg-chart-3/10 text-chart-3"
                    : undefined
                }
              >
                {ok ? <BadgeCheckIcon /> : <AlertCircleIcon />}
                Daytona {formatCheckLabel(key)}
              </Badge>
            ))}
          </div>
        </Field>

        <Field orientation="responsive" className="py-4">
          <FieldContent>
            <FieldTitle>Guidance</FieldTitle>
            <FieldDescription>
              Actionable runtime recommendations.
            </FieldDescription>
          </FieldContent>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-right text-xs text-muted-foreground">
            {runtimeGuidance.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Field>
      </FieldGroup>
    </div>
  );
}

export function shouldHydrateRuntimeForm(
  snapshot: { values?: Record<string, string> } | undefined,
  hasUnsavedRuntimeChanges: boolean,
): boolean {
  return Boolean(snapshot) && !hasUnsavedRuntimeChanges;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
