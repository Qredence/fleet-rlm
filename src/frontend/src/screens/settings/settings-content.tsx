import { useEffect, useMemo, useState } from "react";
import { Bell, Bot, Cpu, Moon, Paintbrush, Sun } from "lucide-react";
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
import {
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { telemetryClient } from "@/lib/telemetry/client";
import { cn } from "@/lib/utils";
import { RuntimeForm } from "@/screens/settings/runtime-form";
import {
  computeLmRuntimeUpdates,
  useRuntimeSettings,
} from "@/screens/settings/use-runtime-settings";

export const settingsSections = [
  { key: "appearance", label: "Appearance", icon: Paintbrush },
  { key: "telemetry", label: "Telemetry", icon: Bell },
  { key: "litellm", label: "LiteLLM Integration", icon: Bot },
  { key: "runtime", label: "Runtime", icon: Cpu },
] as const;

export type SettingsSection = (typeof settingsSections)[number]["key"];

export const sectionDescriptions: Record<SettingsSection, string> = {
  appearance: "Control theme and interface appearance.",
  telemetry: "Configure anonymous telemetry preferences.",
  litellm: "Manage LiteLLM-compatible runtime model and provider integration settings.",
  runtime: "Configure runtime credentials and run Modal/LM connection tests.",
};

const SETTINGS_FIELD_CLASSNAME = "border-b border-border-subtle py-4 last:border-b-0";

export function resolveSettingsSection(section?: string): SettingsSection | undefined {
  return section && settingsSections.some((entry) => entry.key === section)
    ? (section as SettingsSection)
    : undefined;
}

export function getSettingsSectionTitle(section?: SettingsSection): string {
  return settingsSections.find((entry) => entry.key === section)?.label ?? "Settings";
}

export function getSettingsSectionDescription(section?: SettingsSection): string {
  return (
    (section ? sectionDescriptions[section] : undefined) ??
    "Configure theme, telemetry, LM integration, and runtime connectivity."
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

interface SettingsSidebarNavProps {
  section?: SettingsSection;
  onSectionChange: (section?: SettingsSection) => void;
}

export function SettingsSidebarNav({ section, onSectionChange }: SettingsSidebarNavProps) {
  return (
    <SidebarContent>
      <SidebarGroup>
        <SidebarGroupContent>
          <SidebarMenu>
            {settingsSections.map(({ key, label, icon: Icon }) => (
              <SidebarMenuItem key={key}>
                <SidebarMenuButton
                  isActive={section === key || (section == null && key === "appearance")}
                  tooltip={label}
                  onClick={() => onSectionChange(key)}
                >
                  <Icon />
                  <span>{label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    </SidebarContent>
  );
}

interface SettingsSectionContentProps {
  isDark: boolean;
  onToggleTheme: () => void;
  section?: SettingsSection;
}

export function SettingsSectionContent({
  isDark,
  onToggleTheme,
  section,
}: SettingsSectionContentProps) {
  return section === "runtime" ? (
    <RuntimeForm />
  ) : (
    <GroupedSettingsPane isDark={isDark} onToggleTheme={onToggleTheme} section={section} />
  );
}

interface GroupedSettingsPaneProps {
  isDark: boolean;
  onToggleTheme: () => void;
  section?: SettingsSection;
}

export function GroupedSettingsPane({ isDark, onToggleTheme, section }: GroupedSettingsPaneProps) {
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
  const [baselineDelegateLmSmallModel, setBaselineDelegateLmSmallModel] = useState("");

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
  const showAllSections = section == null;
  const showSection = (key: SettingsSection) => showAllSections || section === key;

  return (
    <div>
      {showSection("appearance") ? (
        <>
          {showAllSections ? (
            <div className="border-b border-border-subtle py-3">
              <span className="text-sm font-medium text-muted-foreground">Appearance</span>
            </div>
          ) : null}

          <FieldGroup className="gap-0">
            <Field
              className={cn(SETTINGS_FIELD_CLASSNAME, section === "appearance" && "border-b-0")}
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
                  <Sun aria-hidden="true" />
                  Light
                </ToggleGroupItem>
                <ToggleGroupItem value="dark" aria-label="Dark mode">
                  <Moon aria-hidden="true" />
                  Dark
                </ToggleGroupItem>
              </ToggleGroup>
            </Field>
          </FieldGroup>
        </>
      ) : null}

      {showSection("telemetry") ? (
        <>
          {showAllSections ? (
            <div className="border-b border-border-subtle py-3">
              <span className="text-sm font-medium text-muted-foreground">Telemetry</span>
            </div>
          ) : null}

          <FieldGroup className="gap-0">
            <Field className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Anonymous telemetry</FieldTitle>
                <FieldDescription>
                  Share anonymous usage telemetry to help improve Fleet-RLM. This preference now
                  updates web PostHog capture immediately and propagates to backend AI analytics for
                  new chat turns.
                </FieldDescription>
              </FieldContent>
              <Switch
                className="self-start"
                checked={telemetryEnabled}
                onCheckedChange={(value) => {
                  setTelemetryEnabled(value);
                  telemetryClient.setAnonymousTelemetryEnabled(value);
                  telemetryClient.capture("telemetry_preference_updated", {
                    enabled: value,
                    scope: "anonymous_only_web",
                    source: "grouped_settings",
                  });
                  toast.success(
                    value ? "Anonymous telemetry enabled" : "Anonymous telemetry disabled",
                  );
                }}
              />
            </Field>

            <Field
              className={cn(SETTINGS_FIELD_CLASSNAME, section === "telemetry" && "border-b-0")}
            >
              <FieldContent>
                <FieldTitle>Telemetry scope</FieldTitle>
                <FieldDescription>
                  No account/billing/profile settings are exposed here in v0.4.8. This surface is
                  intentionally limited to functional runtime and privacy controls.
                </FieldDescription>
              </FieldContent>
              <Badge className="self-start" variant="secondary">
                Anonymous-only
              </Badge>
            </Field>
          </FieldGroup>
        </>
      ) : null}

      {showSection("litellm") ? (
        <>
          {showAllSections ? (
            <div className="border-b border-border-subtle py-3">
              <span className="text-sm font-medium text-muted-foreground">LiteLLM Integration</span>
            </div>
          ) : null}

          <FieldGroup className="gap-0">
            <Field className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>LiteLLM integration</FieldTitle>
                <FieldDescription>
                  Configure a custom LiteLLM-compatible endpoint and API key for planner/provider
                  routing. These values are saved through the runtime settings API when local writes
                  are enabled.
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
                  Provider or proxy key used for LM requests. Leave unchanged to keep the current
                  value.
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

            <Field className="py-4">
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
        </>
      ) : null}

      {showSection("runtime") ? <RuntimeForm /> : null}
    </div>
  );
}
