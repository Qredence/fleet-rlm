import {
  Bot,
  Cpu,
  User,
  Bell,
  Settings as SettingsIcon,
  CreditCard,
  Paintbrush,
  Database,
  Info,
} from "lucide-react";

// ── Settings categories ─────────────────────────────────────────────
export const categories = [
  { key: "account", label: "Account", icon: User },
  { key: "billing", label: "Billing", icon: CreditCard },
  { key: "general", label: "General", icon: SettingsIcon },
  { key: "notifications", label: "Notifications", icon: Bell },
  { key: "personalization", label: "Personalization", icon: Paintbrush },
  { key: "data", label: "Data & Privacy", icon: Database },
  { key: "about", label: "About", icon: Info },
] as const;

export type Category = (typeof categories)[number]["key"];

// ── Live settings sections (v0.4.8) ────────────────────────────────
export const settingsSections = [
  { key: "appearance", label: "Appearance", icon: Paintbrush },
  { key: "telemetry", label: "Telemetry", icon: Bell },
  { key: "litellm", label: "LiteLLM Integration", icon: Bot },
  { key: "runtime", label: "Runtime", icon: Cpu },
] as const;

export type SettingsSection = (typeof settingsSections)[number]["key"];
