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
  appearance: "Theme and interface defaults.",
  telemetry: "Privacy and communication preferences.",
  litellm: "Set planner models, provider endpoint, and API key.",
  runtime: "Manage runtime credentials and connectivity checks.",
};

const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
const SETTINGS_SECTION_CLASSNAME = "max-w-[44rem] gap-4";

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
    <SidebarContent className="bg-sidebar/20">
      <SidebarGroup className="flex h-full flex-col gap-2 p-4">
        <SidebarGroupContent>
          <SidebarMenu className="gap-1.5">
            {settingsSections.map(({ key, label, icon: Icon }) => (
              <SidebarMenuItem key={key}>
                <SidebarMenuButton
                  isActive={section === key || (section == null && key === "appearance")}
                  size="default"
                  tooltip={label}
                  onClick={() => onSectionChange(key)}
                  className="h-10 gap-3 rounded-xl px-3 font-medium text-sidebar-foreground/78 shadow-none data-[active=true]:bg-sidebar-accent/90 data-[active=true]:text-sidebar-accent-foreground"
                >
                  <Icon className="text-sidebar-foreground/65 group-data-[active=true]/menu-button:text-sidebar-accent-foreground" />
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
  const appearanceLegend = showAllSections ? "Appearance" : "General";
  const telemetryLegend = showAllSections ? "Telemetry" : "Communication preferences";
  const liteLlmLegend = showAllSections ? "LiteLLM Integration" : "Model routing";

  return (
    <div className="flex flex-col gap-10">
      {showSection("appearance") ? (
        <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
          <div className="flex flex-col gap-1">
            <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
              {appearanceLegend}
            </FieldLegend>
            <FieldDescription>
              {showAllSections
                ? sectionDescriptions.appearance
                : "Choose how Fleet looks during focused work."}
            </FieldDescription>
          </div>

          <FieldGroup className="gap-0">
            <Field className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Theme</FieldTitle>
                <FieldDescription>
                  Choose the interface appearance for the web app.
                </FieldDescription>
              </FieldContent>
              <ToggleGroup
                type="single"
                variant="card"
                value={isDark ? "dark" : "light"}
                aria-label="Theme mode"
                className="mt-1 flex w-full flex-wrap gap-4"
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
                <ToggleGroupItem
                  value="light"
                  aria-label="Light mode"
                  className="group/theme-item min-w-[8.5rem] flex-col items-start gap-3"
                >
                  <span className="flex h-14 w-full min-w-[8.5rem] items-start rounded-lg border border-border-subtle bg-white p-3 shadow-xs">
                    <span className="flex w-full gap-2">
                      <span className="w-4 rounded-md bg-zinc-100" />
                      <span className="flex flex-1 flex-col gap-1.5 pt-0.5">
                        <span className="h-1.5 w-9 rounded-full bg-zinc-200" />
                        <span className="h-1.5 w-12 rounded-full bg-zinc-100" />
                      </span>
                    </span>
                  </span>
                  <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Sun aria-hidden="true" />
                    Light
                  </span>
                </ToggleGroupItem>
                <ToggleGroupItem
                  value="dark"
                  aria-label="Dark mode"
                  className="group/theme-item min-w-[8.5rem] flex-col items-start gap-3"
                >
                  <span className="flex h-14 w-full min-w-[8.5rem] items-start rounded-lg border border-zinc-800 bg-zinc-950 p-3 shadow-xs">
                    <span className="flex w-full gap-2">
                      <span className="w-4 rounded-md bg-zinc-800" />
                      <span className="flex flex-1 flex-col gap-1.5 pt-0.5">
                        <span className="h-1.5 w-9 rounded-full bg-zinc-600" />
                        <span className="h-1.5 w-12 rounded-full bg-zinc-800" />
                      </span>
                    </span>
                  </span>
                  <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Moon aria-hidden="true" />
                    Dark
                  </span>
                </ToggleGroupItem>
              </ToggleGroup>
            </Field>
          </FieldGroup>
        </FieldSet>
      ) : null}

      {showSection("telemetry") ? (
        <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
          <div className="flex flex-col gap-1">
            <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
              {telemetryLegend}
            </FieldLegend>
            <FieldDescription>{sectionDescriptions.telemetry}</FieldDescription>
          </div>

          <FieldGroup className="gap-0">
            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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
        </FieldSet>
      ) : null}

      {showSection("litellm") ? (
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
                  routing. These values are saved through the runtime settings API when local writes
                  are enabled.
                </FieldDescription>
              </FieldContent>
            </Field>

            {!writeEnabled ? (
              <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
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

            <Field orientation="responsive" className="gap-5 py-5">
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
      ) : null}

      {showSection("runtime") ? <RuntimeForm /> : null}
    </div>
  );
}
