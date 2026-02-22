/**
 * SettingsPaneContent — shared grouped settings surface for page + dialog.
 *
 * v0.4.8 intentionally hides placeholder categories and exposes only
 * functional settings relevant to appearance, telemetry, and LM runtime
 * integration.
 */
import { GroupedSettingsPane } from "@/features/settings/GroupedSettingsPane";

interface SettingsPaneContentProps {
  isDark: boolean;
  onToggleTheme: () => void;
}

export function SettingsPaneContent({
  isDark,
  onToggleTheme,
}: SettingsPaneContentProps) {
  return <GroupedSettingsPane isDark={isDark} onToggleTheme={onToggleTheme} />;
}
