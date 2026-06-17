import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
  Select,
  SelectContent,
  SelectItem,
  SelectPositioner,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { useLlmProfileModels, useLlmRoleBindings } from "./use-llm-profiles";
import { modelMatchesCatalog } from "./llm-profiles/constants";

export { shouldHydrateRuntimeForm, errorMessage } from "./runtime-status-panel";

type RuntimeField = {
  key: string;
  label: string;
  description: string;
  isSecret?: boolean;
  placeholder?: string;
};

type RuntimeModelRole = "planner" | "delegate" | "delegate_small";

const RUNTIME_MODEL_ROLES: Record<string, RuntimeModelRole> = {
  DSPY_LM_MODEL: "planner",
  DSPY_DELEGATE_LM_MODEL: "delegate",
  DSPY_DELEGATE_LM_SMALL_MODEL: "delegate_small",
};

function isRuntimeSecretKey(key: string, secretKeys: readonly string[]): boolean {
  return secretKeys.includes(key);
}

function runtimeEnvModelValue(modelId: string): string {
  if (!modelId) return "";
  if (modelId.startsWith("models/")) {
    return `openai/${modelId.slice("models/".length)}`;
  }
  if (modelId.startsWith("gemini-") && !modelId.includes("/")) {
    return `openai/${modelId}`;
  }
  if (modelId.startsWith("gemini/gemini-")) {
    return `openai/${modelId.slice("gemini/".length)}`;
  }
  return modelId;
}

function RuntimeModelSelect({
  id,
  label,
  role,
  value,
  placeholder,
  onValueChange,
}: {
  id: string;
  label: string;
  role: RuntimeModelRole;
  value: string;
  placeholder?: string;
  onValueChange: (value: string) => void;
}) {
  const rolesQuery = useLlmRoleBindings();
  const binding = rolesQuery.data?.bindings?.find((candidate) => candidate.role === role);
  const profileId = binding?.profile_id ?? null;
  const modelsQuery = useLlmProfileModels(profileId);
  const catalogModels = modelsQuery.data?.models ?? [];
  const currentCatalogModel = catalogModels.find(
    (model) =>
      modelMatchesCatalog(value, model.id) || runtimeEnvModelValue(model.id) === value.trim(),
  );
  const currentSelectValue = currentCatalogModel
    ? runtimeEnvModelValue(currentCatalogModel.id)
    : value;
  const currentDisplayLabel = currentCatalogModel?.label ?? currentSelectValue;
  const options = currentSelectValue
    ? [
        {
          id: currentSelectValue,
          label: currentCatalogModel?.label ?? currentSelectValue,
        },
        ...catalogModels
          .map((model) => ({
            id: runtimeEnvModelValue(model.id),
            label: model.label,
          }))
          .filter((model) => model.id !== currentSelectValue),
      ]
    : catalogModels.map((model) => ({
        id: runtimeEnvModelValue(model.id),
        label: model.label,
      }));

  return (
    <Select
      value={currentSelectValue}
      onValueChange={(nextValue) => onValueChange(nextValue ?? "")}
      disabled={rolesQuery.isPending || (!!profileId && modelsQuery.isPending)}
    >
      <SelectTrigger id={id} className="w-full min-w-0 sm:max-w-md" aria-label={label}>
        <SelectValue
          placeholder={
            profileId
              ? modelsQuery.isPending
                ? "Loading models..."
                : (placeholder ?? "Select model")
              : value || "No provider profile assigned"
          }
        >
          {currentDisplayLabel || undefined}
        </SelectValue>
      </SelectTrigger>
      <SelectPositioner>
        <SelectContent>
          {options.map((model) => (
            <SelectItem key={model.id} value={model.id}>
              {model.label}
            </SelectItem>
          ))}
        </SelectContent>
      </SelectPositioner>
    </Select>
  );
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

      <FieldGroup className="gap-0">
        <Field orientation="responsive" className="border-b border-border-subtle py-5">
          <FieldContent>
            <FieldTitle>Runtime Configuration</FieldTitle>
            <FieldDescription>
              Update runtime credentials, Daytona connectivity, and model selection used by the
              local backend.
            </FieldDescription>
          </FieldContent>
        </Field>
        {runtimeFields.map((field) => {
          const secretKey = isRuntimeSecretKey(field.key, runtimeSecretKeys) ? field.key : null;
          const inputId = `runtime-${field.key.toLowerCase()}`;
          const inputValue = formValues[field.key] ?? "";
          const modelRole = RUNTIME_MODEL_ROLES[field.key];
          return (
            <Field
              key={field.key}
              orientation="responsive"
              className="border-b border-border-subtle py-5"
            >
              <FieldContent>
                <FieldLabel htmlFor={inputId}>{field.label}</FieldLabel>
                <FieldDescription>{field.description}</FieldDescription>
                {field.isSecret && secretKey ? (
                  <FieldDescription>
                    Write-only input. Configured value:{" "}
                    {maskedValues[secretKey] ? maskedValues[secretKey] : "not set"}.
                  </FieldDescription>
                ) : null}
              </FieldContent>
              <div className="flex min-w-0 flex-1 justify-start sm:justify-end">
                {field.isSecret && secretKey ? (
                  <InputGroup className="w-full min-w-0 sm:max-w-md">
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
                        variant={clearSecretFlags[secretKey] ? "secondary" : "outline"}
                        aria-pressed={clearSecretFlags[secretKey] ?? false}
                        onClick={() => toggleClearSecret(secretKey)}
                      >
                        {clearSecretFlags[secretKey] ? "Will clear on save" : "Clear saved value"}
                      </InputGroupButton>
                    </InputGroupAddon>
                  </InputGroup>
                ) : modelRole ? (
                  <RuntimeModelSelect
                    id={inputId}
                    label={field.label}
                    role={modelRole}
                    value={inputValue}
                    placeholder={field.placeholder}
                    onValueChange={(value) => updateFieldValue(field.key, value)}
                  />
                ) : (
                  <Input
                    id={inputId}
                    type="text"
                    value={inputValue}
                    placeholder={field.placeholder}
                    autoComplete="off"
                    aria-label={field.label}
                    onChange={(event) => updateFieldValue(field.key, event.currentTarget.value)}
                    className="w-full min-w-0 sm:max-w-md"
                  />
                )}
              </div>
            </Field>
          );
        })}
        <Field orientation="responsive" className="py-5">
          <FieldContent>
            <FieldDescription>
              Writes to <code>.env</code> (local only), updates process env, and refreshes the
              active runtime configuration.
            </FieldDescription>
          </FieldContent>
          <Button
            variant="secondary"
            className="rounded-lg"
            onClick={handleSave}
            disabled={saveDisabled}
          >
            {saveSettings.isPending ? "Saving…" : "Save settings"}
          </Button>
        </Field>
      </FieldGroup>

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
