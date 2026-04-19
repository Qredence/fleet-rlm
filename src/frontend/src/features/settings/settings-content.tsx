import { useEffect, useState } from "react";
import { Bell, Bot, Cpu, Moon, Paintbrush, Sparkles, Sun } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
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
import { OptimizationForm } from "@/features/optimization/optimization-form";
import { RuntimeForm } from "./runtime-form";
import { LiteLlmForm } from "./litellm-form";

export const settingsSections = [
  { key: "appearance", label: "Appearance", icon: Paintbrush },
  { key: "telemetry", label: "Telemetry", icon: Bell },
  { key: "litellm", label: "LiteLLM Integration", icon: Bot },
  { key: "runtime", label: "Runtime", icon: Cpu },
  { key: "optimization", label: "Optimization", icon: Sparkles },
] as const;

export type SettingsSection = (typeof settingsSections)[number]["key"];

export const sectionDescriptions: Record<SettingsSection, string> = {
  appearance: "Theme and interface defaults.",
  telemetry: "Privacy and communication preferences.",
  litellm: "Set planner models, provider endpoint, and API key.",
  runtime: "Manage runtime credentials and connectivity checks.",
  optimization:
    "Configure GEPA prompt optimization. Use the Optimization surface for datasets, runs, and comparisons.",
};

const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
const SETTINGS_SECTION_CLASSNAME = "max-w-content gap-4";

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
  if (section === "runtime") return <RuntimeForm />;
  if (section === "optimization") return <OptimizationForm />;
  return <GroupedSettingsPane isDark={isDark} onToggleTheme={onToggleTheme} section={section} />;
}

interface GroupedSettingsPaneProps {
  isDark: boolean;
  onToggleTheme: () => void;
  section?: SettingsSection;
}

export function GroupedSettingsPane({ isDark, onToggleTheme, section }: GroupedSettingsPaneProps) {
  const [telemetryEnabled, setTelemetryEnabled] = useState(true);

  useEffect(() => {
    setTelemetryEnabled(telemetryClient.isAnonymousTelemetryEnabled());
  }, []);

  const showAllSections = section == null;
  const showSection = (key: SettingsSection) => showAllSections || section === key;
  const appearanceLegend = showAllSections ? "Appearance" : "General";
  const telemetryLegend = showAllSections ? "Telemetry" : "Communication preferences";

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
                  className="group/theme-item min-w-34 flex-col items-start gap-3"
                >
                  <span className="flex h-14 w-full min-w-34 items-start rounded-lg border border-border-subtle bg-white p-3 shadow-xs">
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
                  className="group/theme-item min-w-34 flex-col items-start gap-3"
                >
                  <span className="flex h-14 w-full min-w-34 items-start rounded-lg border border-zinc-800 bg-zinc-950 p-3 shadow-xs">
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

            <Field className={SETTINGS_FIELD_CLASSNAME}>
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

      <LiteLlmForm showAllSections={showAllSections} section={section} />

      {showSection("runtime") ? <RuntimeForm /> : null}

      {showSection("optimization") ? <OptimizationForm /> : null}
    </div>
  );
}
