import type { SettingsSection } from "./screen/settings-content";
import { ProviderProfilesPanel } from "./llm-profiles/provider-profiles-panel";

interface LiteLlmFormProps {
  showAllSections: boolean;
  section?: SettingsSection;
}

export function LiteLlmForm(props: LiteLlmFormProps) {
  return <ProviderProfilesPanel {...props} />;
}
