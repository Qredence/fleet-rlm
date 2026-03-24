import { PanelRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils";
import { useNavigationStore } from "@/stores/navigationStore";

export function ShellHeader() {
  const { activeNav, isCanvasOpen, toggleCanvas } = useNavigationStore();
  const isMobile = useIsMobile();

  const titleMap: Record<string, string> = {
    workspace: "RLM Workspace",
    volumes: "Volumes",
    settings: "Settings",
  };
  const title = titleMap[activeNav] || "Dashboard";

  return (
    <header
      className={cn(
        "flex shrink-0 items-center justify-between gap-3 border-b border-border-subtle bg-background/95 backdrop-blur-sm",
        isMobile
          ? "px-3 py-2 pt-[max(env(safe-area-inset-top,0px),0.5rem)]"
          : "px-4 py-2",
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <SidebarTrigger className={isMobile ? "size-9 rounded-xl" : "size-8"} />
        <div className="min-w-0 truncate text-sm font-medium text-foreground">
          {title}
        </div>
      </div>

      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Button
              type="button"
              size={isMobile ? "icon" : "icon-sm"}
              variant={isCanvasOpen ? "secondary" : "ghost"}
              aria-label={isCanvasOpen ? "Close side panel" : "Open side panel"}
              className={cn(isMobile && "rounded-xl")}
              onClick={toggleCanvas}
            >
              <PanelRight />
              <span className="sr-only">
                {isCanvasOpen ? "Close side panel" : "Open side panel"}
              </span>
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {isCanvasOpen ? "Close side panel" : "Open side panel"}
        </TooltipContent>
      </Tooltip>
    </header>
  );
}
