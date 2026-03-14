import { Lightbulb } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";
import {
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ACTION_BUTTON_SIZE,
} from "./composerActionStyles";

interface ThinkButtonProps {
  enabled: boolean;
  onToggle: () => void;
}

function ThinkButton({ enabled, onToggle }: ThinkButtonProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            onClick={onToggle}
            aria-pressed={enabled}
            size={PROMPT_INPUT_ACTION_BUTTON_SIZE}
            variant="ghost"
            className={cn(
              PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
              enabled
                ? "bg-accent/15 text-accent hover:bg-accent/20"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Lightbulb className="h-3.5 w-3.5" />
            <span className="text-xs font-medium">Think</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top">
          <p className="text-xs">{enabled ? "Disable" : "Enable"} extended thinking</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { ThinkButton };
