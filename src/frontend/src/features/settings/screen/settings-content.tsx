import { useEffect, useState, useMemo } from "react";
import { Bell, Bot, Cpu, Info, LogOut, Moon, Paintbrush, Sun, User } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { useAuth } from "@/lib/auth/auth-context";
import { isNeonAuthConfigured, neonAuthClient } from "@/lib/auth/neon";
import { RuntimeForm } from "../runtime-form";
import { LiteLlmForm } from "../litellm-form";
import { ServiceInfoPanel } from "../service-info-panel";

export const settingsSections = [
  { key: "appearance", label: "Appearance", icon: Paintbrush },
  { key: "telemetry", label: "Telemetry", icon: Bell },
  { key: "litellm", label: "LLM Providers", icon: Bot },
  { key: "runtime", label: "Runtime", icon: Cpu },
  { key: "about", label: "About", icon: Info },
] as const;

export type SettingsSection = "account" | (typeof settingsSections)[number]["key"];

export const sectionDescriptions: Record<SettingsSection, string> = {
  account: "Manage your profile details, active sessions, and credentials.",
  appearance: "Theme and interface defaults.",
  telemetry: "Privacy and communication preferences.",
  litellm: "Manage provider profiles, role model assignments, and API credentials.",
  runtime: "Manage runtime credentials and connectivity checks.",
  about: "Build metadata and active feature flags for this instance.",
};

export function useGetSettingsSections() {
  const { isAuthenticated } = useAuth();
  const neonConfigured = isNeonAuthConfigured();

  return useMemo(() => {
    const list = [];
    if (neonConfigured && isAuthenticated) {
      list.push({ key: "account" as const, label: "Account", icon: User });
    }
    list.push(
      { key: "appearance" as const, label: "Appearance", icon: Paintbrush },
      { key: "telemetry" as const, label: "Telemetry", icon: Bell },
      { key: "litellm" as const, label: "LLM Providers", icon: Bot },
      { key: "runtime" as const, label: "Runtime", icon: Cpu },
      { key: "about" as const, label: "About", icon: Info },
    );
    return list;
  }, [isAuthenticated, neonConfigured]);
}

const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
const SETTINGS_SECTION_CLASSNAME = "max-w-content gap-4";

export function resolveSettingsSection(section?: string): SettingsSection | undefined {
  const allowedKeys: SettingsSection[] = [
    "account",
    "appearance",
    "telemetry",
    "litellm",
    "runtime",
    "about",
  ];
  return section && allowedKeys.includes(section as SettingsSection)
    ? (section as SettingsSection)
    : undefined;
}

export function getSettingsSectionTitle(section?: SettingsSection): string {
  if (section === "account") return "Account";
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
  const sections = useGetSettingsSections();
  const { isAuthenticated, logout } = useAuth();
  return (
    <SidebarContent className="bg-sidebar/20 justify-between">
      <SidebarGroup className="flex flex-col gap-2 p-4 shrink-0">
        <SidebarGroupContent>
          <SidebarMenu className="gap-1.5">
            {sections.map(({ key, label, icon: Icon }) => (
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

      {isAuthenticated && (
        <SidebarGroup className="p-4 border-t border-sidebar-border/50 shrink-0">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  size="default"
                  tooltip="Logout"
                  onClick={() => logout()}
                  className="h-10 gap-3 rounded-xl px-3 font-medium text-destructive hover:bg-destructive/10 hover:text-destructive shadow-none"
                >
                  <LogOut className="size-4" />
                  <span>Logout</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      )}
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
  if (section === "account") {
    return <AccountSettingsPane />;
  }
  if (section === "runtime") return <RuntimeForm />;
  if (section === "about") return <AboutPane />;
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
            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Theme</FieldTitle>
                <FieldDescription>
                  Choose the interface appearance for the web app.
                </FieldDescription>
              </FieldContent>
              <ToggleGroup
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

      <LiteLlmForm showAllSections={showAllSections} section={section} />

      {showSection("runtime") ? <RuntimeForm /> : null}

      {showSection("about") ? <AboutPane /> : null}
    </div>
  );
}

function AboutPane() {
  return (
    <FieldSet className="max-w-content gap-4">
      <div className="flex flex-col gap-1">
        <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
          About this instance
        </FieldLegend>
        <FieldDescription>{sectionDescriptions.about}</FieldDescription>
      </div>
      <FieldGroup className="gap-0">
        <ServiceInfoPanel />
      </FieldGroup>
    </FieldSet>
  );
}

function AccountSettingsPane() {
  const { user, refresh } = useAuth();
  const [name, setName] = useState(user?.name || "");
  const [isSavingName, setIsSavingName] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  // Sync state if user details change
  useEffect(() => {
    if (user?.name) {
      setName(user.name);
    }
  }, [user?.name]);

  const handleSaveName = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      toast.error("Please enter a valid name");
      return;
    }
    setIsSavingName(true);
    try {
      const { error } = await neonAuthClient!.updateUser({ name: name.trim() });
      if (error) {
        toast.error(error.message || "Failed to update profile name");
      } else {
        toast.success("Profile name updated successfully");
        if (refresh) await refresh();
      }
    } catch {
      toast.error("An unexpected error occurred");
    } finally {
      setIsSavingName(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPassword || !newPassword || !confirmPassword) {
      toast.error("Please fill in all password fields");
      return;
    }
    if (newPassword.length < 6) {
      toast.error("New password must be at least 6 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error("New passwords do not match");
      return;
    }
    setIsChangingPassword(true);
    try {
      const { error } = await neonAuthClient!.changePassword({
        currentPassword,
        newPassword,
        revokeOtherSessions: false,
      });
      if (error) {
        toast.error(error.message || "Failed to change password");
      } else {
        toast.success("Password updated successfully");
        setCurrentPassword("");
        setNewPassword("");
        setConfirmPassword("");
      }
    } catch {
      toast.error("An unexpected error occurred");
    } finally {
      setIsChangingPassword(false);
    }
  };

  return (
    <div className="flex flex-col gap-10">
      <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
        <div className="flex flex-col gap-1">
          <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
            Profile settings
          </FieldLegend>
          <FieldDescription>Manage your public name and email configuration.</FieldDescription>
        </div>

        <FieldGroup className="gap-0">
          <form onSubmit={handleSaveName} className="w-full">
            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Display Name</FieldTitle>
                <FieldDescription>
                  Enter your full name or display name (maximum 32 characters).
                </FieldDescription>
                <div className="mt-3 max-w-sm">
                  <Input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={32}
                    placeholder="E.g. John Doe"
                  />
                </div>
              </FieldContent>
              <Button
                type="submit"
                disabled={isSavingName || name.trim() === user?.name}
                className="self-end"
              >
                {isSavingName ? "Saving..." : "Save"}
              </Button>
            </Field>
          </form>

          <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Email Address</FieldTitle>
              <FieldDescription>
                The primary email address used to access your Fleet account.
              </FieldDescription>
              <div className="mt-3 max-w-sm">
                <Input
                  type="email"
                  value={user?.email || ""}
                  disabled
                  className="opacity-70 cursor-not-allowed"
                />
              </div>
            </FieldContent>
            <Badge className="self-center" variant="secondary">
              Verified
            </Badge>
          </Field>
        </FieldGroup>
      </FieldSet>

      <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
        <div className="flex flex-col gap-1">
          <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
            Security settings
          </FieldLegend>
          <FieldDescription>
            Change your account password to ensure your data stays protected.
          </FieldDescription>
        </div>

        <FieldGroup className="gap-0">
          <form onSubmit={handleChangePassword} className="w-full">
            <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent className="flex flex-col gap-4 max-w-md">
                <div>
                  <FieldTitle>Current Password</FieldTitle>
                  <div className="mt-1.5">
                    <Input
                      type="password"
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                    />
                  </div>
                </div>

                <div>
                  <FieldTitle>New Password</FieldTitle>
                  <div className="mt-1.5">
                    <Input
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                    />
                  </div>
                </div>

                <div>
                  <FieldTitle>Confirm New Password</FieldTitle>
                  <div className="mt-1.5">
                    <Input
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                    />
                  </div>
                </div>
              </FieldContent>
              <Button
                type="submit"
                disabled={
                  isChangingPassword || !currentPassword || !newPassword || !confirmPassword
                }
                className="self-end"
              >
                {isChangingPassword ? "Updating..." : "Update Password"}
              </Button>
            </Field>
          </form>
        </FieldGroup>
      </FieldSet>
    </div>
  );
}
