import type { ComponentType } from "react";
import { Brain, Sparkles, Wrench } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import { PROMPT_INPUT_ACTION_BUTTON_CLASSNAME } from "./composerActionStyles";

interface ExecutionModeOption {
  id: WsExecutionMode;
  name: string;
  icon: ComponentType<{ className?: string }>;
}

const EXECUTION_MODE_OPTIONS: ExecutionModeOption[] = [
  { id: "auto", name: "Auto", icon: Sparkles },
  { id: "rlm_only", name: "RLM only", icon: Brain },
  { id: "tools_only", name: "Tools only", icon: Wrench },
];

interface ExecutionModeDropdownProps {
  value: WsExecutionMode;
  onChange: (mode: WsExecutionMode) => void;
}

function ExecutionModeDropdown({
  value,
  onChange,
}: ExecutionModeDropdownProps) {
  const currentMode =
    EXECUTION_MODE_OPTIONS.find((option) => option.id === value) ??
    EXECUTION_MODE_OPTIONS[0]!;

  return (
    <Select
      value={value}
      onValueChange={(nextValue) => onChange(nextValue as WsExecutionMode)}
    >
      <SelectTrigger
        size="sm"
        className={cn(
          PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
          "w-auto min-w-0 justify-center border-transparent shadow-none",
        )}
        aria-label={`Execution mode: ${currentMode.name}`}
      >
        <span className="font-app flex-none text-(length:--font-text-sm-size) leading-(--font-text-sm-line-height) tracking-(--font-text-sm-tracking)">
          {currentMode.name}
        </span>
      </SelectTrigger>
      <SelectContent align="end" alignItemWithTrigger={false} className="w-44">
        <SelectGroup>
          {EXECUTION_MODE_OPTIONS.map((option) => {
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

export { ExecutionModeDropdown };
