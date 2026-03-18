import { useEffect, useMemo, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils/cn";
import { RuntimePane } from "@/screens/settings/runtime-pane";
import { errorMessage } from "@/screens/settings/settings-errors";
import type { SettingsSection } from "@/screens/settings/settings-types";
import {
  computeLmRuntimeUpdates,
  useRuntimeSettings,
} from "@/screens/settings/hooks/use-runtime-settings";
import { telemetryClient } from "@/lib/telemetry/client";

interface GroupedSettingsPaneProps {
  isDark: boolean;
  onToggleTheme: () => void;
  section?: SettingsSection;
}

const SETTINGS_FIELD_CLASSNAME =
  "border-b border-border-subtle py-4 last:border-b-0";

export function GroupedSettingsPane({
  isDark,
  onToggleTheme,
  section,
}: GroupedSettingsPaneProps) {
  const { settingsQuery, statusQuery, saveSettings } = useRuntimeSettings();

  const [telemetryEnabled, setTelemetryEnabled] = useState(true);
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
  const [baselineDelegateLmSmallModel, setBaselineDelegateLmSmallModel] =
    useState("");

  useEffect(() => {
    setTelemetryEnabled(telemetryClient.isAnonymousTelemetryEnabled());
  }, []);

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

  const runtimeUpdates = useMemo(() => {
    return computeLmRuntimeUpdates(
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
    );
  }, [
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
  ]);

  const dirtyKeys = useMemo(
    () => Object.keys(runtimeUpdates),
    [runtimeUpdates],
  );
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
          description:
            updated.length > 0
              ? `Updated: ${updated.join(", ")}`
              : "No keys changed.",
        });
      },
      onError: (error) => {
        toast.error("Failed to save LM integration settings", {
          description: errorMessage(error),
        });
      },
    });
  };

  const saveDisabled =
    dirtyKeys.length === 0 || saveSettings.isPending || !writeEnabled;
  const showAllSections = section == null;
  const showSection = (key: SettingsSection) =>
    showAllSections || section === key;

  return (
    <div>
      {showSection("appearance") && (
        <>
          {showAllSections && (
            <div className="py-3 border-b border-border-subtle">
              <span className="text-sm text-muted-foreground font-medium">
                Appearance
              </span>
            </div>
          )}

          <FieldGroup className="gap-0">
            <Field
              orientation="responsive"
              className={cn(
                SETTINGS_FIELD_CLASSNAME,
                section === "appearance" && "border-b-0",
              )}
            >
              <FieldContent>
                <FieldTitle>Theme</FieldTitle>
                <FieldDescription>
                  Choose the interface appearance for the web app.
                </FieldDescription>
              </FieldContent>
              <ToggleGroup
                type="single"
                size="sm"
                variant="outline"
                value={isDark ? "dark" : "light"}
                aria-label="Theme mode"
                onValueChange={(nextValue) => {
                  if (nextValue === "light" && isDark) {
                    onToggleTheme();
                    toast.success("Switched to Light mode");
                  }
                  if (nextValue === "dark" && !isDark) {
                    onToggleTheme();
                    toast.success("Switched to Dark mode");
                  }
                }}
              >
                <ToggleGroupItem value="light" aria-label="Light mode">
                  <Sun className="size-4" aria-hidden="true" />
                  Light
                </ToggleGroupItem>
                <ToggleGroupItem value="dark" aria-label="Dark mode">
                  <Moon className="size-4" aria-hidden="true" />
                  Dark
                </ToggleGroupItem>
              </ToggleGroup>
            </Field>
          </FieldGroup>
        </>
      )}

      {showSection("telemetry") && (
        <>
          {showAllSections && (
            <div className="py-3 border-b border-border-subtle">
              <span className="text-sm text-muted-foreground font-medium">
                Telemetry
              </span>
            </div>
          )}

          <FieldGroup className="gap-0">
            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>Anonymous telemetry</FieldTitle>
                <FieldDescription>
                  Share anonymous usage telemetry to help improve Fleet-RLM.
                  This preference now updates web PostHog capture immediately
                  and propagates to backend AI analytics for new chat turns.
                </FieldDescription>
              </FieldContent>
              <Switch
                checked={telemetryEnabled}
                onCheckedChange={(val) => {
                  setTelemetryEnabled(val);
                  telemetryClient.setAnonymousTelemetryEnabled(val);
                  telemetryClient.capture("telemetry_preference_updated", {
                    enabled: val,
                    scope: "anonymous_only_web",
                    source: "grouped_settings",
                  });
                  toast.success(
                    val
                      ? "Anonymous telemetry enabled"
                      : "Anonymous telemetry disabled",
                  );
                }}
              />
            </Field>

            <Field
              orientation="responsive"
              className={cn(
                SETTINGS_FIELD_CLASSNAME,
                section === "telemetry" && "border-b-0",
              )}
            >
              <FieldContent>
                <FieldTitle>Telemetry scope</FieldTitle>
                <FieldDescription>
                  No account/billing/profile settings are exposed here in
                  v0.4.8. This surface is intentionally limited to functional
                  runtime and privacy controls.
                </FieldDescription>
              </FieldContent>
              <Badge variant="secondary">Anonymous-only</Badge>
            </Field>
          </FieldGroup>
        </>
      )}

      {showSection("litellm") && (
        <>
          {showAllSections && (
            <div className="py-3 border-b border-border-subtle">
              <span className="text-sm text-muted-foreground font-medium">
                LiteLLM Integration
              </span>
            </div>
          )}

          <FieldGroup className="gap-0">
            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>LiteLLM integration</FieldTitle>
                <FieldDescription>
                  Configure a custom LiteLLM-compatible endpoint and API key for
                  planner/provider routing. These values are saved through the
                  runtime settings API when local writes are enabled.
                </FieldDescription>
              </FieldContent>
            </Field>

            {!writeEnabled ? (
              <Field
                orientation="responsive"
                className={SETTINGS_FIELD_CLASSNAME}
              >
                <FieldContent>
                  <FieldTitle>Write Protection</FieldTitle>
                  <FieldDescription>
                    Runtime settings updates are disabled because APP_ENV is not
                    local.
                  </FieldDescription>
                </FieldContent>
                <Badge variant="destructive-subtle">Read-only</Badge>
              </Field>
            ) : null}

            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>Planner LM model</FieldTitle>
                <FieldDescription>
                  Primary planner model identifier used for chat turns and
                  planning.
                </FieldDescription>
              </FieldContent>
              <Input
                type="text"
                value={lmModel}
                placeholder=""
                autoComplete="off"
                aria-label="Planner LM model"
                onChange={(event) => setLmModel(event.target.value)}
                className="w-[260px] max-w-[50vw]"
              />
            </Field>

            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>Delegate LM model</FieldTitle>
                <FieldDescription>
                  Optional delegate model used for recursive or long-context
                  sub-agent tasks.
                </FieldDescription>
              </FieldContent>
              <Input
                type="text"
                value={delegateLmModel}
                placeholder=""
                autoComplete="off"
                aria-label="Delegate LM model"
                onChange={(event) => setDelegateLmModel(event.target.value)}
                className="w-[260px] max-w-[50vw]"
              />
            </Field>

            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>Delegate small LM model</FieldTitle>
                <FieldDescription>
                  Optional lightweight delegate model for fast/low-cost
                  operations.
                </FieldDescription>
              </FieldContent>
              <Input
                type="text"
                value={delegateLmSmallModel}
                placeholder=""
                autoComplete="off"
                aria-label="Delegate small LM model"
                onChange={(event) =>
                  setDelegateLmSmallModel(event.target.value)
                }
                className="w-[260px] max-w-[50vw]"
              />
            </Field>

            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>Custom API endpoint</FieldTitle>
                <FieldDescription>
                  Optional LiteLLM (or provider proxy) base URL.
                </FieldDescription>
              </FieldContent>
              <Input
                type="text"
                value={apiBase}
                placeholder=""
                autoComplete="off"
                aria-label="Custom API endpoint"
                onChange={(event) => setApiBase(event.target.value)}
                className="w-[260px] max-w-[50vw]"
              />
            </Field>

            <Field
              orientation="responsive"
              className={SETTINGS_FIELD_CLASSNAME}
            >
              <FieldContent>
                <FieldTitle>API key</FieldTitle>
                <FieldDescription>
                  Provider or proxy key used for LM requests. Leave unchanged to
                  keep the current value.
                </FieldDescription>
              </FieldContent>
              <div className="flex w-full max-w-[min(100%,20rem)] flex-col gap-2">
                <InputGroup className="w-full">
                  <InputGroupInput
                    type="password"
                    value={apiKeyInput}
                    placeholder=""
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
                      {clearApiKeyOnSave
                        ? "Will clear on save"
                        : "Clear saved value"}
                    </InputGroupButton>
                  </InputGroupAddon>
                </InputGroup>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <span className="text-right text-xs text-muted-foreground">
                    Write-only input. Configured value:{" "}
                    {maskedApiKey || "not set"}.
                  </span>
                </div>
              </div>
            </Field>

            <Field orientation="responsive" className="py-4">
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
                className="rounded-lg"
                onClick={handleSaveLmSettings}
                disabled={saveDisabled}
              >
                {saveSettings.isPending ? "Saving…" : "Save settings"}
              </Button>
            </Field>
          </FieldGroup>
        </>
      )}

      {showSection("runtime") && <RuntimePane />}
    </div>
  );
}
