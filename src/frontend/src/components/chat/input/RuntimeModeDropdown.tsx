import type { ComponentType } from "react";
import { FlaskConical, MessagesSquare } from "lucide-react";

import type { WsRuntimeMode } from "@/lib/rlm-api/wsTypes";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import { MenuItem } from "@/components/ui/menu-item";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils/cn";
import {
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ACTION_BUTTON_SIZE,
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
  const checkedIndex = RUNTIME_MODE_OPTIONS.findIndex((option) => option.id === value);
  const currentMode =
    RUNTIME_MODE_OPTIONS.find((option) => option.id === value) ?? RUNTIME_MODE_OPTIONS[0]!;
  const CurrentModeIcon = currentMode.icon;

  return (
    <Popover>
      <PopoverTrigger asChild>
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
      </PopoverTrigger>

      <PopoverContent align="end" className="w-48 p-0">
        <Dropdown checkedIndex={checkedIndex} className="w-full">
          {RUNTIME_MODE_OPTIONS.map((option, index) => (
            <MenuItem
              key={option.id}
              icon={option.icon}
              label={option.name}
              index={index}
              checked={value === option.id}
              onSelect={() => onChange(option.id)}
            />
          ))}
        </Dropdown>
      </PopoverContent>
    </Popover>
  );
}

export { RuntimeModeDropdown };
