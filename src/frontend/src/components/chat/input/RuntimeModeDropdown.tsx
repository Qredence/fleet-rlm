import type { ComponentType } from "react";
import { FlaskConical, MessagesSquare } from "lucide-react";

import type { WsRuntimeMode } from "@/lib/rlm-api/wsTypes";
import { Button } from "@/components/ui/button";
import {
  Menubar,
  MenubarContent,
  MenubarMenu,
  MenubarRadioGroup,
  MenubarRadioItem,
  MenubarTrigger,
} from "@/components/ui/menubar";
import { cn } from "@/lib/utils/cn";
import {
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ACTION_BUTTON_SIZE,
  PROMPT_INPUT_MENUBAR_CLASSNAME,
  PROMPT_INPUT_MENU_CONTENT_CLASSNAME,
} from "./composerActionStyles";

interface RuntimeModeOption {
  id: WsRuntimeMode;
  name: string;
  icon: ComponentType<{ className?: string }>;
}

const RUNTIME_MODE_OPTIONS: RuntimeModeOption[] = [
  {
    id: "modal_chat",
    name: "Modal chat",
    icon: MessagesSquare,
  },
  {
    id: "daytona_pilot",
    name: "Daytona pilot",
    icon: FlaskConical,
  },
];

interface RuntimeModeDropdownProps {
  value: WsRuntimeMode;
  onChange: (mode: WsRuntimeMode) => void;
}

function RuntimeModeDropdown({ value, onChange }: RuntimeModeDropdownProps) {
  const currentMode =
    RUNTIME_MODE_OPTIONS.find((option) => option.id === value) ?? RUNTIME_MODE_OPTIONS[0]!;
  const CurrentModeIcon = currentMode.icon;

  return (
    <Menubar className={PROMPT_INPUT_MENUBAR_CLASSNAME}>
      <MenubarMenu>
        <MenubarTrigger asChild>
          <Button
            type="button"
            size={PROMPT_INPUT_ACTION_BUTTON_SIZE}
            variant="ghost"
            className={cn(
              PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
              "justify-center gap-2 text-muted-foreground hover:text-foreground",
            )}
            aria-label={`Runtime mode: ${currentMode.name}`}
          >
            <CurrentModeIcon className="size-4 shrink-0" />
            <span className="font-app text-(length:--font-text-sm-size) leading-(--font-text-sm-line-height) tracking-(--font-text-sm-tracking)">
              {currentMode.name}
            </span>
          </Button>
        </MenubarTrigger>

        <MenubarContent align="end" className={cn(PROMPT_INPUT_MENU_CONTENT_CLASSNAME, "w-48")}>
          <MenubarRadioGroup value={value}>
            {RUNTIME_MODE_OPTIONS.map((option) => (
              <MenubarRadioItem
                key={option.id}
                value={option.id}
                showIndicator={false}
                onSelect={() => onChange(option.id)}
                className={cn(
                  "prompt-composer-menu-item cursor-pointer gap-3 rounded-xl px-3 py-2.5",
                  value === option.id && "prompt-composer-menu-item-active",
                )}
              >
                <option.icon className="prompt-composer-menu-icon h-4 w-4" />
                <span className="text-sm">{option.name}</span>
              </MenubarRadioItem>
            ))}
          </MenubarRadioGroup>
        </MenubarContent>
      </MenubarMenu>
    </Menubar>
  );
}

export { RuntimeModeDropdown };
