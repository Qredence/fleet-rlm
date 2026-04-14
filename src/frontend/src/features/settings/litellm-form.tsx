import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { computeLmRuntimeUpdates, useRuntimeSettings } from "./use-runtime-settings";
import type { SettingsSection } from "./settings-content";
import { sectionDescriptions } from "./settings-content";

const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
const SETTINGS_SECTION_CLASSNAME = "max-w-[44rem] gap-4";

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

interface LiteLlmFormProps {
  showAllSections: boolean;
  section?: SettingsSection;
}

export function LiteLlmForm({ showAllSections, section }: LiteLlmFormProps) {
  const { settingsQuery, statusQuery, saveSettings } = useRuntimeSettings();

  const [apiBase, setApiBase] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [maskedApiKey, setMaskedApiKey] = useState("");
  const [clearApiKeyOnSave, setClearApiKeyOnSave] = useState(false);
  const [lmModel, setLmModel] = useState("");
  const [delegateLmModel, setDelegateLmModel] = useState("");
  const [delegateLmSmallModel, setDelegateLmSmallModel] = useState("");
  const [baselineApiBase, setBaselineApiBase] = useState("");
  const [baselineLmModel, setBaselineLmModel] = useState("");
  const [baselineDelegateLmModel, setBaselineDelegateLmModel] = useState("");
  const [baselineDelegateLmSmallModel, setBaselineDelegateLmSmallModel] = useState("");

  useEffect(() => {
    const values = settingsQuery.data?.values;
    if (!values) return;
    const nextLmModel = values.DSPY_LM_MODEL ?? "";
    const nextDelegateLmModel = values.DSPY_DELEGATE_LM_MODEL ?? "";
    const nextDelegateLmSmallModel = values.DSPY_DELEGATE_LM_SMALL_MODEL ?? "";
    const nextApiBase = values.DSPY_LM_API_BASE ?? "";
    const nextApiKey = values.DSPY_LLM_API_KEY ?? "";
    setLmModel(nextLmModel);
    setDelegateLmModel(nextDelegateLmModel);
    setDelegateLmSmallModel(nextDelegateLmSmallModel);
    setApiBase(nextApiBase);
    setApiKeyInput("");
    setMaskedApiKey(nextApiKey);
    setClearApiKeyOnSave(false);
    setBaselineLmModel(nextLmModel);
    setBaselineDelegateLmModel(nextDelegateLmModel);
    setBaselineDelegateLmSmallModel(nextDelegateLmSmallModel);
    setBaselineApiBase(nextApiBase);
  }, [settingsQuery.data]);

  const runtimeUpdates = useMemo(
    () =>
      computeLmRuntimeUpdates(
        {
          DSPY_LM_MODEL: lmModel,
          DSPY_DELEGATE_LM_MODEL: delegateLmModel,
          DSPY_DELEGATE_LM_SMALL_MODEL: delegateLmSmallModel,
          DSPY_LM_API_BASE: apiBase,
        },
        {
          DSPY_LM_MODEL: baselineLmModel,
          DSPY_DELEGATE_LM_MODEL: baselineDelegateLmModel,
          DSPY_DELEGATE_LM_SMALL_MODEL: baselineDelegateLmSmallModel,
          DSPY_LM_API_BASE: baselineApiBase,
        },
        {
          secretInputs: {
            DSPY_LLM_API_KEY: apiKeyInput,
          },
          clearedSecrets: clearApiKeyOnSave ? ["DSPY_LLM_API_KEY"] : [],
        },
      ),
    [
      apiBase,
      apiKeyInput,
      baselineApiBase,
      baselineDelegateLmModel,
      baselineDelegateLmSmallModel,
      baselineLmModel,
      clearApiKeyOnSave,
      delegateLmModel,
      delegateLmSmallModel,
      lmModel,
    ],
  );

  const dirtyKeys = useMemo(() => Object.keys(runtimeUpdates), [runtimeUpdates]);
  const status = statusQuery.data;
  const writeEnabled = status?.write_enabled !== false;

  const handleSaveLmSettings = () => {
    if (!writeEnabled) {
      toast.error("Runtime settings are read-only in this environment");
      return;
    }
    if (dirtyKeys.length === 0) {
      toast("No LM integration changes to save");
      return;
    }
    saveSettings.mutate(runtimeUpdates, {
      onSuccess: (result) => {
        const updated = result.updated ?? [];
        setBaselineLmModel(lmModel);
        setBaselineDelegateLmModel(delegateLmModel);
        setBaselineDelegateLmSmallModel(delegateLmSmallModel);
        setBaselineApiBase(apiBase);
        setMaskedApiKey((currentMaskedApiKey) =>
          clearApiKeyOnSave ? "" : apiKeyInput.trim() !== "" ? "[REDACTED]" : currentMaskedApiKey,
        );
        setApiKeyInput("");
        setClearApiKeyOnSave(false);
        toast.success("LM integration settings saved", {
          description: updated.length > 0 ? `Updated: ${updated.join(", ")}` : "No keys changed.",
        });
      },
      onError: (error) => {
        toast.error("Failed to save LM integration settings", {
          description: errorMessage(error),
        });
      },
    });
  };

  const saveDisabled = dirtyKeys.length === 0 || saveSettings.isPending || !writeEnabled;
  const showSection = (key: SettingsSection) => showAllSections || section === key;
  const liteLlmLegend = showAllSections ? "LiteLLM Integration" : "Model routing";

  if (!showSection("litellm")) return null;

  return (
    <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
      <div className="flex flex-col gap-1">
        <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
          {liteLlmLegend}
        </FieldLegend>
        <FieldDescription>{sectionDescriptions.litellm}</FieldDescription>
      </div>

      <FieldGroup className="gap-0">
        <Field className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>LiteLLM integration</FieldTitle>
            <FieldDescription>
              Configure a custom LiteLLM-compatible endpoint and API key for planner/provider
              routing. These values are saved through the runtime settings API when local writes are
              enabled.
            </FieldDescription>
          </FieldContent>
        </Field>

        {!writeEnabled ? (
          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Write Protection</FieldTitle>
              <FieldDescription>
                Runtime settings updates are disabled because APP_ENV is not local.
              </FieldDescription>
            </FieldContent>
            <Badge className="self-start" variant="destructive">
              Read-only
            </Badge>
          </Field>
        ) : null}

        <Field className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Planner LM model</FieldTitle>
            <FieldDescription>
              Primary planner model identifier used for chat turns and planning.
            </FieldDescription>
          </FieldContent>
          <Input
            type="text"
            value={lmModel}
            autoComplete="off"
            aria-label="Planner LM model"
            onChange={(event) => setLmModel(event.target.value)}
            className="w-full"
          />
        </Field>

        <Field className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Delegate LM model</FieldTitle>
            <FieldDescription>
              Optional delegate model used for recursive or long-context sub-agent tasks.
            </FieldDescription>
          </FieldContent>
          <Input
            type="text"
            value={delegateLmModel}
            autoComplete="off"
            aria-label="Delegate LM model"
            onChange={(event) => setDelegateLmModel(event.target.value)}
            className="w-full"
          />
        </Field>

        <Field className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Delegate small LM model</FieldTitle>
            <FieldDescription>
              Optional lightweight delegate model for fast/low-cost operations.
            </FieldDescription>
          </FieldContent>
          <Input
            type="text"
            value={delegateLmSmallModel}
            autoComplete="off"
            aria-label="Delegate small LM model"
            onChange={(event) => setDelegateLmSmallModel(event.target.value)}
            className="w-full"
          />
        </Field>

        <Field className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Custom API endpoint</FieldTitle>
            <FieldDescription>Optional LiteLLM (or provider proxy) base URL.</FieldDescription>
          </FieldContent>
          <Input
            type="text"
            value={apiBase}
            autoComplete="off"
            aria-label="Custom API endpoint"
            onChange={(event) => setApiBase(event.target.value)}
            className="w-full"
          />
        </Field>

        <Field className={SETTINGS_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>API key</FieldTitle>
            <FieldDescription>
              Provider or proxy key used for LM requests. Leave unchanged to keep the current value.
            </FieldDescription>
          </FieldContent>
          <div className="flex w-full flex-col gap-2">
            <InputGroup className="w-full">
              <InputGroupInput
                type="password"
                value={apiKeyInput}
                autoComplete="off"
                aria-label="API key"
                onChange={(event) => {
                  setApiKeyInput(event.target.value);
                  setClearApiKeyOnSave(false);
                }}
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  type="button"
                  size="sm"
                  variant={clearApiKeyOnSave ? "secondary" : "outline"}
                  aria-pressed={clearApiKeyOnSave}
                  className="h-full rounded-none border-y-0 border-r-0 border-l border-border-subtle/70 px-4 shadow-none"
                  onClick={() => {
                    const nextClear = !clearApiKeyOnSave;
                    setClearApiKeyOnSave(nextClear);
                    if (nextClear) {
                      setApiKeyInput("");
                    }
                  }}
                >
                  {clearApiKeyOnSave ? "Will clear on save" : "Clear saved value"}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="text-right text-xs text-muted-foreground">
                Write-only input. Configured value: {maskedApiKey || "not set"}.
              </span>
            </div>
          </div>
        </Field>

        <Field className="gap-5 py-5">
          <FieldContent>
            <FieldTitle>Save LM integration settings</FieldTitle>
            <FieldDescription>
              {status
                ? `Environment: ${status.app_env}. Saves via /api/v1/runtime/settings when local writes are enabled.`
                : "Saves via /api/v1/runtime/settings when local writes are enabled."}
            </FieldDescription>
          </FieldContent>
          <Button
            variant="secondary"
            className="self-start rounded-lg"
            onClick={handleSaveLmSettings}
            disabled={saveDisabled}
          >
            {saveSettings.isPending ? "Saving…" : "Save settings"}
          </Button>
        </Field>
      </FieldGroup>
    </FieldSet>
  );
}
