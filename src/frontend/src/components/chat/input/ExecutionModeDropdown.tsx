import type { ReactNode } from "react";
import { Bot, Brain, Check, ChevronDown, Sparkles, Wrench } from "lucide-react";

import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils/cn";
import {
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ACTION_BUTTON_SIZE,
} from "./composerActionStyles";

interface ExecutionModeOption {
  id: WsExecutionMode;
  name: string;
  description: string;
  icon: ReactNode;
}

const EXECUTION_MODE_OPTIONS: ExecutionModeOption[] = [
  {
    id: "auto",
    name: "Auto",
    description: "Let the agent decide when to delegate with RLM.",
    icon: <Sparkles className="h-4 w-4" />,
  },
  {
    id: "rlm_only",
    name: "RLM only",
    description: "Force the turn through recursive long-context delegation.",
    icon: <Brain className="h-4 w-4" />,
  },
  {
    id: "tools_only",
    name: "Tools only",
    description: "Use normal tools only and skip RLM delegation helpers.",
    icon: <Wrench className="h-4 w-4" />,
  },
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
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size={PROMPT_INPUT_ACTION_BUTTON_SIZE}
          variant="ghost"
          className={cn(
            PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
            "h-8 gap-1.5 text-muted-foreground hover:text-foreground",
          )}
          aria-label={`Execution mode: ${currentMode.name}`}
        >
          <Bot className="h-3.5 w-3.5" />
          <span style={{ fontSize: "var(--text-label)" }}>
            {currentMode.name}
          </span>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="w-72 border-border bg-popover"
      >
        <DropdownMenuLabel className="px-2.5 py-1.5 text-[10px] font-medium text-muted-foreground">
          Execution mode
        </DropdownMenuLabel>

        {EXECUTION_MODE_OPTIONS.map((option) => (
          <DropdownMenuItem
            key={option.id}
            onClick={() => onChange(option.id)}
            className={cn(
              "flex items-start justify-between gap-3 rounded-md px-2.5 py-2 text-xs cursor-pointer",
              value === option.id && "bg-accent",
            )}
          >
            <div className="flex min-w-0 items-start gap-2">
              <span className="mt-0.5 text-muted-foreground">
                {option.icon}
              </span>
              <div className="min-w-0">
                <div className="font-medium text-foreground">{option.name}</div>
                <div className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
                  {option.description}
                </div>
              </div>
            </div>

            {value === option.id ? (
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export { ExecutionModeDropdown };
