import { SlidersHorizontal } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import type { SettingsSection } from "@/features/settings/types";
import {
  PROMPT_INPUT_ICON_BUTTON_CLASSNAME,
  PROMPT_INPUT_ICON_BUTTON_VARIANT,
} from "./composerActionStyles";

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
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <IconButton
            type="button"
            aria-label="Open runtime settings"
            variant={PROMPT_INPUT_ICON_BUTTON_VARIANT}
            className={PROMPT_INPUT_ICON_BUTTON_CLASSNAME}
            onClick={handleOpenSettings}
          >
            <SlidersHorizontal className="size-5" />
          </IconButton>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={6}>
        Runtime settings
      </TooltipContent>
    </Tooltip>
  );
}

export { SettingsDropdown };
