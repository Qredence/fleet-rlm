import { Bot, Cpu, Bell, Paintbrush } from "lucide-react";

// ── Live settings sections ──────────────────────────────────────────

export const settingsSections = [
  { key: "appearance", label: "Appearance", icon: Paintbrush },
  { key: "telemetry", label: "Telemetry", icon: Bell },
  { key: "litellm", label: "LiteLLM Integration", icon: Bot },
  { key: "runtime", label: "Runtime", icon: Cpu },
] as const;

export type SettingsSection = (typeof settingsSections)[number]["key"];

/** Per-section description used in dialog headers and page subtitles. */
export const sectionDescriptions: Record<SettingsSection, string> = {
  appearance: "Control theme and interface appearance.",
  telemetry: "Configure anonymous telemetry preferences.",
  litellm: "Manage LiteLLM-compatible runtime model and provider integration settings.",
  runtime: "Configure runtime credentials and run Modal/LM connection tests.",
};
