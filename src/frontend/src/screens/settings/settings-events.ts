import type { SettingsSection } from "@/screens/settings/settings-screen";

export const OPEN_SETTINGS_EVENT = "open-settings";

export type OpenSettingsEventDetail = {
  section?: SettingsSection;
  returnFocusTarget?: HTMLElement | null;
};

export function requestSettingsDialogOpen(
  detail: OpenSettingsEventDetail = {},
): boolean {
  const openSettingsEvent = new CustomEvent<OpenSettingsEventDetail>(
    OPEN_SETTINGS_EVENT,
    {
      detail,
      cancelable: true,
    },
  );

  return document.dispatchEvent(openSettingsEvent) === false;
}
