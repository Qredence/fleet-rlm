import type { ComponentType } from "react";
import { Brain, Sparkles, Wrench } from "lucide-react";

import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import { MenuItem } from "@/components/ui/menu-item";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils/cn";
import {
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ACTION_BUTTON_SIZE,
} from "./composerActionStyles";

interface ExecutionModeOption {
  id: WsExecutionMode;
  name: string;
  icon: ComponentType<{ className?: string }>;
}

const EXECUTION_MODE_OPTIONS: ExecutionModeOption[] = [
  {
    id: "auto",
    name: "Auto",
    icon: Sparkles,
  },
  {
    id: "rlm_only",
    name: "RLM only",
    icon: Brain,
  },
  {
    id: "tools_only",
    name: "Tools only",
    icon: Wrench,
  },
];

interface ExecutionModeDropdownProps {
  value: WsExecutionMode;
  onChange: (mode: WsExecutionMode) => void;
}

function ExecutionModeDropdown({ value, onChange }: ExecutionModeDropdownProps) {
  const checkedIndex = EXECUTION_MODE_OPTIONS.findIndex((option) => option.id === value);
  const currentMode =
    EXECUTION_MODE_OPTIONS.find((option) => option.id === value) ?? EXECUTION_MODE_OPTIONS[0]!;
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
          aria-label={`Execution mode: ${currentMode.name}`}
        >
          <CurrentModeIcon className="size-4 shrink-0" />
          <span className="font-app text-(length:--font-text-sm-size) leading-(--font-text-sm-line-height) tracking-(--font-text-sm-tracking)">
            {currentMode.name}
          </span>
        </Button>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-44 p-0">
        <Dropdown checkedIndex={checkedIndex} className="w-full">
          {EXECUTION_MODE_OPTIONS.map((option, index) => (
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

export { ExecutionModeDropdown };
