/**
 * SettingsPaneContent — shared grouped settings surface for page + dialog.
 *
 * v0.4.8 intentionally hides placeholder categories and exposes only
 * functional settings relevant to appearance, telemetry, and LM runtime
 * integration.
 */
import { GroupedSettingsPane } from "@/features/settings/GroupedSettingsPane";
import type { SettingsSection } from "@/features/settings/types";

interface SettingsPaneContentProps {
  isDark: boolean;
  onToggleTheme: () => void;
  section?: SettingsSection;
}

export function SettingsPaneContent({
  isDark,
  onToggleTheme,
  section,
}: SettingsPaneContentProps) {
  return (
    <GroupedSettingsPane
      isDark={isDark}
      onToggleTheme={onToggleTheme}
      section={section}
    />
  );
}
