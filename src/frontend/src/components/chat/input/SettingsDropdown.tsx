import { SlidersHorizontal } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import type { SettingsSection } from "@/features/settings/types";

interface OpenSettingsEventDetail {
  section?: SettingsSection;
}

function SettingsDropdown() {
  const { navigate } = useAppNavigate();

  const handleOpenSettings = () => {
    const openSettingsEvent = new CustomEvent<OpenSettingsEventDetail>(
      "open-settings",
      {
        detail: { section: "runtime" },
        cancelable: true,
      },
    );

    const wasHandledByDialog =
      document.dispatchEvent(openSettingsEvent) === false;

    if (!wasHandledByDialog) {
      navigate("/settings?section=runtime");
    }
  };

  return (
    <span className="inline-flex">
      <IconButton
        type="button"
        aria-label="Open runtime settings"
        className="touch-target rounded-full"
        onClick={handleOpenSettings}
      >
        <SlidersHorizontal className="size-5 text-foreground" />
      </IconButton>
    </span>
  );
}

export { SettingsDropdown };
