import { Settings } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import { Dropdown } from "@/components/ui/dropdown";
import { MenuItem } from "@/components/ui/menu-item";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import {
  PROMPT_INPUT_ICON_BUTTON_CLASSNAME,
  PROMPT_INPUT_ICON_BUTTON_VARIANT,
} from "./composerActionStyles";

interface OpenSettingsEventDetail {
  section?: string;
}

function SettingsDropdown() {
  const { navigate } = useAppNavigate();

  const handleOpenSettings = () => {
    const openSettingsEvent = new CustomEvent<OpenSettingsEventDetail>("open-settings", {
      detail: { section: "runtime" },
      cancelable: true,
    });

    const wasHandledByDialog = document.dispatchEvent(openSettingsEvent) === false;

    if (!wasHandledByDialog) {
      navigate({ to: "/settings", search: { section: "runtime" } });
    }
  };

  return (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <IconButton
              type="button"
              aria-label="Runtime settings"
              variant={PROMPT_INPUT_ICON_BUTTON_VARIANT}
              className={`${PROMPT_INPUT_ICON_BUTTON_CLASSNAME} w-8.5 min-w-8.5`}
            >
              <Settings className="size-4" />
            </IconButton>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="top" sideOffset={6}>
          Runtime settings
        </TooltipContent>
      </Tooltip>

      <PopoverContent align="start" className="w-64 p-0">
        <Dropdown className="w-full">
          <MenuItem
            icon={Settings}
            label="Open runtime settings"
            index={0}
            onSelect={handleOpenSettings}
            className="gap-3"
          />
        </Dropdown>
      </PopoverContent>
    </Popover>
  );
}

export { SettingsDropdown };
