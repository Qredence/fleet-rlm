import type { ReactNode } from "react";
import { Brain, Sparkles, Wrench } from "lucide-react";

import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import { Button } from "@/components/ui/button";
import {
  Menubar,
  MenubarContent,
  MenubarLabel,
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
    <Menubar className={PROMPT_INPUT_MENUBAR_CLASSNAME}>
      <MenubarMenu>
        <MenubarTrigger asChild>
          <Button
            type="button"
            size={PROMPT_INPUT_ACTION_BUTTON_SIZE}
            variant="ghost"
            className={cn(
              PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
              "justify-center text-muted-foreground hover:text-foreground",
            )}
            aria-label={`Execution mode: ${currentMode.name}`}
          >
            <span className="font-app text-(length:--font-text-sm-size) leading-(--font-text-sm-line-height) tracking-(--font-text-sm-tracking)">
              {currentMode.name}
            </span>
          </Button>
        </MenubarTrigger>

        <MenubarContent
          align="end"
          className={cn(PROMPT_INPUT_MENU_CONTENT_CLASSNAME, "w-72")}
        >
          <MenubarLabel className="prompt-composer-menu-label px-3 py-2 uppercase tracking-[0.12em]">
            Execution mode
          </MenubarLabel>

          <MenubarRadioGroup value={value}>
            {EXECUTION_MODE_OPTIONS.map((option) => (
              <MenubarRadioItem
                key={option.id}
                value={option.id}
                onSelect={() => onChange(option.id)}
                className={cn(
                  "prompt-composer-menu-item flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5 pl-8 text-xs",
                  value === option.id && "prompt-composer-menu-item-active",
                )}
              >
                <span className="prompt-composer-menu-icon mt-0.5">
                  {option.icon}
                </span>
                <div className="min-w-0">
                  <div className="font-medium text-(--color-text)">
                    {option.name}
                  </div>
                  <div className="mt-0.5 text-[11px] leading-4 text-(--color-text-secondary)">
                    {option.description}
                  </div>
                </div>
              </MenubarRadioItem>
            ))}
          </MenubarRadioGroup>
        </MenubarContent>
      </MenubarMenu>
    </Menubar>
  );
}

export { ExecutionModeDropdown };
