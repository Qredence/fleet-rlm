/**
 * Re-export from the decomposed settings module.
 *
 * The live settings surface now centers on:
 *
 *   features/settings/
 *     ├── SettingsDialog.tsx      — Dialog/drawer shell
 *     ├── SettingsPaneContent.tsx — Shared page/dialog entry point
 *     ├── GroupedSettingsPane.tsx — Current grouped settings implementation
 *     ├── RuntimePane.tsx         — Runtime-specific controls
 *     ├── SettingsToggleRow.tsx   — Reusable toggle-row form element
 *     └── types.ts                — Category definitions
 */
export { SettingsDialog } from "@/features/settings/SettingsDialog";
