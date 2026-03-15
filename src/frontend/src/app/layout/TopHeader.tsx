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
        "flex shrink-0 items-center justify-between gap-4 h-14",
        !isMobile && "border-b border-border-subtle/80 bg-background/95 px-6",
        isMobile && "px-4 pt-2"
      )}
    >
      <div className="flex items-center gap-2">
        <h1 className="text-xs font-medium text-foreground tracking-wide">{title}</h1>
      </div>

      <div className="flex items-center shrink-0">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                onClick={toggleCanvas}
                isActive={isCanvasOpen}
                aria-label={isCanvasOpen ? "Close side panel" : "Open side panel"}
                className={isMobile ? "touch-target" : "h-8 w-8 rounded-lg hover:bg-background/80"}
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
