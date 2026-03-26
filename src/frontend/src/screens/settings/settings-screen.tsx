import { useSearch, useRouter } from "@tanstack/react-router";
import { SettingsDialog } from "@/components/settings-dialog";
import { resolveSettingsSection } from "@/screens/settings/settings-content";

export {
  GroupedSettingsPane,
  getSettingsSectionDescription,
  getSettingsSectionTitle,
  resolveSettingsSection,
  sectionDescriptions,
  settingsSections,
  SettingsSectionContent,
  SettingsSidebarNav,
} from "@/screens/settings/settings-content";
export type { SettingsSection } from "@/screens/settings/settings-content";

export function SettingsScreen() {
  const router = useRouter();
  const searchParams = useSearch({ strict: false }) as { section?: string };

  const selectedSection = resolveSettingsSection(searchParams.section);

  return (
    <div className="flex h-full items-center justify-center p-4 md:p-6">
      <SettingsDialog
        open
        section={selectedSection}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            router.history.back();
          }
        }}
      />
    </div>
  );
}
