import { Lightbulb } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";

interface ThinkButtonProps {
  enabled: boolean;
  onToggle: () => void;
}

function ThinkButton({ enabled, onToggle }: ThinkButtonProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onToggle}
            aria-pressed={enabled}
            className={cn(
              "inline-flex items-center gap-1 h-7 px-2.5 rounded-lg text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
              enabled
                ? "text-accent bg-accent/15 hover:bg-accent/20"
                : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
            )}
          >
            <Lightbulb className="h-3.5 w-3.5" />
            <span className="text-xs font-medium">Think</span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="top">
          <p className="text-xs">
            {enabled ? "Disable" : "Enable"} extended thinking
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { ThinkButton };
