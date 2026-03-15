import { PanelRight } from "lucide-react";
import { useNavigationStore } from "@/stores/navigationStore";
import { useIsMobile } from "@/hooks/useIsMobile";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/lib/utils/cn";

export function TopHeader() {
  const { activeNav, isCanvasOpen, toggleCanvas } = useNavigationStore();
  const isMobile = useIsMobile();

  // Format the activeNav into a nice title
  const titleMap: Record<string, string> = {
    workspace: "RLM Workspace",
    volumes: "Volumes",
  };
  const title = titleMap[activeNav] || "Dashboard";

  return (
    <header
      className={cn(
        "flex shrink-0 items-center justify-between gap-2 h-12",
        !isMobile && "border-b border-border-subtle bg-surface px-4",
        isMobile && "px-4 pt-2 bg-surface border-b border-border-subtle"
      )}
    >
      <div className="flex-1 min-w-0 text-sm font-medium text-foreground truncate">
        {title}
      </div>

      <div className="flex items-center shrink-0">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                onClick={toggleCanvas}
                isActive={isCanvasOpen}
                aria-label={isCanvasOpen ? "Close side panel" : "Open side panel"}
                className={isMobile ? "touch-target" : "h-8 w-8 rounded-lg hover:bg-muted/80"}
              >
                <PanelRight className={isMobile ? "size-6" : "size-5"} />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {isCanvasOpen ? "Close side panel" : "Open side panel"}
          </TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
