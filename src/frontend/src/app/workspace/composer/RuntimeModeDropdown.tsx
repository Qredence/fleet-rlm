import type { ComponentType } from "react";
import { FlaskConical, MessagesSquare } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { WsRuntimeMode } from "@/lib/rlm-api/wsTypes";
import { PROMPT_INPUT_ACTION_BUTTON_CLASSNAME } from "./composerActionStyles";

interface RuntimeModeOption {
  id: WsRuntimeMode;
  name: string;
  icon: ComponentType<{ className?: string }>;
}

const RUNTIME_MODE_OPTIONS: RuntimeModeOption[] = [
  { id: "modal_chat", name: "Modal chat", icon: MessagesSquare },
  { id: "daytona_pilot", name: "Daytona pilot", icon: FlaskConical },
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
    <Select value={value} onValueChange={(nextValue) => onChange(nextValue as WsRuntimeMode)}>
      <SelectTrigger
        size="sm"
        className={cn(
          PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
          "w-auto min-w-0 justify-center gap-2 border-transparent bg-transparent px-3 text-muted-foreground shadow-none hover:bg-muted hover:text-foreground",
        )}
        aria-label={`Runtime mode: ${currentMode.name}`}
      >
        <div className="flex items-center gap-2">
          <CurrentModeIcon className="size-4 shrink-0" />
          <span className="font-app text-(length:--font-text-sm-size) leading-(--font-text-sm-line-height) tracking-(--font-text-sm-tracking)">
            {currentMode.name}
          </span>
        </div>
      </SelectTrigger>
      <SelectContent align="end" className="w-48">
        <SelectGroup>
          {RUNTIME_MODE_OPTIONS.map((option) => {
            const OptionIcon = option.icon;
            return (
              <SelectItem key={option.id} value={option.id}>
                <div className="flex items-center gap-2">
                  <OptionIcon className="size-4 shrink-0" />
                  <span>{option.name}</span>
                </div>
              </SelectItem>
            );
          })}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

export { RuntimeModeDropdown };
