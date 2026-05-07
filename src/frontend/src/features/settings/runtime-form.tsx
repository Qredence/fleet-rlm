import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardFooter,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
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
  flattenRuntimeSettingsMaskedValues,
  flattenRuntimeSettingsValues,
  runtimeEditableKeysFromSnapshot,
  runtimeSecretKeysFromSnapshot,
  useRuntimeSettings,
  type CategorizedRuntimeSettingsSnapshot,
} from "./use-runtime-settings";
import type { RuntimeStatusResponse } from "@/lib/rlm-api";
import { RuntimeStatusPanel, shouldHydrateRuntimeForm, errorMessage } from "./runtime-status-panel";
import { RuntimeConnectivityPanel } from "./runtime-connectivity-panel";

export { shouldHydrateRuntimeForm, errorMessage } from "./runtime-status-panel";

type RuntimeField = {
  key: string;
  label: string;
  description: string;
  isSecret?: boolean;
  placeholder?: string;
};

function isRuntimeSecretKey(key: string, secretKeys: readonly string[]): boolean {
  return secretKeys.includes(key);
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

  const snapshot = settingsQuery.data as CategorizedRuntimeSettingsSnapshot | undefined;
  const runtimeFields = useMemo<RuntimeField[]>(
    () =>
      (snapshot?.categories ?? []).flatMap((category) =>
        (category.fields ?? [])
          .filter((field) => field.editable)
          .map((field) => ({
            key: field.key,
            label: field.label,
            description: field.description,
            isSecret: field.secret,
            placeholder: field.placeholder ?? field.default ?? undefined,
          })),
      ),
    [snapshot],
  );
  const runtimeEditableKeys = useMemo(() => runtimeEditableKeysFromSnapshot(snapshot), [snapshot]);
  const runtimeSecretKeys = useMemo(() => runtimeSecretKeysFromSnapshot(snapshot), [snapshot]);
  const initialValues = useMemo(() => flattenRuntimeSettingsValues(snapshot), [snapshot]);
  const maskedValues = useMemo(() => flattenRuntimeSettingsMaskedValues(snapshot), [snapshot]);
  const [baselineValues, setBaselineValues] = useState<Record<string, string>>(initialValues);
  const [formValues, setFormValues] = useState<Record<string, string>>(initialValues);
  const [clearSecretFlags, setClearSecretFlags] = useState<Record<string, boolean>>({});

  const clearedSecrets = useMemo(
    () => runtimeSecretKeys.filter((key) => clearSecretFlags[key] === true),
    [clearSecretFlags, runtimeSecretKeys],
  );

  const secretInputs = useMemo(
    () =>
      Object.fromEntries(runtimeSecretKeys.map((key) => [key, formValues[key] ?? ""])) as Partial<
        Record<string, string>
      >,
    [formValues, runtimeSecretKeys],
  );

  const updates = useMemo(
    () =>
      computeRuntimeUpdates(
        formValues,
        baselineValues,
        {
          secretInputs,
          clearedSecrets,
        },
        runtimeEditableKeys,
        runtimeSecretKeys,
      ),
    [
      baselineValues,
      clearedSecrets,
      formValues,
      runtimeEditableKeys,
      runtimeSecretKeys,
      secretInputs,
    ],
  );
  const dirtyKeys = useMemo(() => Object.keys(updates), [updates]);
  const hasUnsavedRuntimeChanges = dirtyKeys.length > 0;
  const status = statusQuery.data;
  const daytonaTest = status?.tests?.daytona;
  const lmTest = status?.tests?.lm;

  useEffect(() => {
    if (!snapshot) return;
    if (!shouldHydrateRuntimeForm(snapshot, hasUnsavedRuntimeChanges)) return;
    const nextBaseline = flattenRuntimeSettingsValues(snapshot);
    const nextFormValues = { ...nextBaseline };
    for (const key of runtimeSecretKeys) {
      nextFormValues[key] = "";
    }
    setBaselineValues(nextBaseline);
    setFormValues(nextFormValues);
    setClearSecretFlags({});
  }, [hasUnsavedRuntimeChanges, runtimeSecretKeys, snapshot]);

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
          for (const key of runtimeSecretKeys) {
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

  const updateFieldValue = (key: string, value: string) => {
    setFormValues((prev) => ({ ...prev, [key]: value }));
    if (isRuntimeSecretKey(key, runtimeSecretKeys)) {
      setClearSecretFlags((prev) => ({
        ...prev,
        [key]: false,
      }));
    }
  };

  const toggleClearSecret = (secretKey: string) => {
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

      <SectionCard variant="subtle">
        <SectionCardHeader className="border-b border-border-subtle/70">
          <SectionCardTitle>Runtime Configuration</SectionCardTitle>
          <SectionCardDescription>
            Update runtime credentials, Daytona connectivity, and model selection used by the local
            backend.
          </SectionCardDescription>
        </SectionCardHeader>
        <SectionCardContent className="pt-6">
          <FieldGroup className="gap-5">
            {runtimeFields.map((field) => {
              const secretKey = isRuntimeSecretKey(field.key, runtimeSecretKeys) ? field.key : null;
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
        </SectionCardContent>
        <SectionCardFooter className="border-t border-border-subtle/70 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
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
        </SectionCardFooter>
      </SectionCard>

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
