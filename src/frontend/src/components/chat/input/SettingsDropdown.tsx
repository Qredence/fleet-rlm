import { Settings } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import {
  Menubar,
  MenubarContent,
  MenubarItem,
  MenubarLabel,
  MenubarMenu,
  MenubarTrigger,
} from "@/components/ui/menubar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import type { SettingsSection } from "@/features/settings/types";
import {
  PROMPT_INPUT_ICON_BUTTON_CLASSNAME,
  PROMPT_INPUT_ICON_BUTTON_VARIANT,
  PROMPT_INPUT_MENUBAR_CLASSNAME,
  PROMPT_INPUT_MENU_CONTENT_CLASSNAME,
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
    <Menubar className={PROMPT_INPUT_MENUBAR_CLASSNAME}>
      <MenubarMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <MenubarTrigger asChild>
                <IconButton
                  type="button"
                  aria-label="Runtime settings"
                  variant={PROMPT_INPUT_ICON_BUTTON_VARIANT}
                  className={`${PROMPT_INPUT_ICON_BUTTON_CLASSNAME} w-8.5 min-w-8.5`}
                >
                  <Settings className="size-4" />
                </IconButton>
              </MenubarTrigger>
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" sideOffset={6}>
            Runtime settings
          </TooltipContent>
        </Tooltip>

        <MenubarContent
          align="start"
          className={cn(PROMPT_INPUT_MENU_CONTENT_CLASSNAME, "w-64")}
        >
          <MenubarLabel className="prompt-composer-menu-label px-3 py-2 uppercase tracking-[0.12em]">
            Runtime settings
          </MenubarLabel>
          <MenubarItem
            className="prompt-composer-menu-item cursor-pointer gap-3 rounded-xl px-3 py-2.5"
            onSelect={handleOpenSettings}
          >
            <Settings className="h-4 w-4" />
            <div className="min-w-0">
              <div className="font-medium text-(--color-text)">
                Open runtime settings
              </div>
              <div className="mt-0.5 text-[11px] leading-4 text-(--color-text-secondary)">
                Adjust model, execution, and session controls.
              </div>
            </div>
          </MenubarItem>
        </MenubarContent>
      </MenubarMenu>
    </Menubar>
  );
}

export { SettingsDropdown };
