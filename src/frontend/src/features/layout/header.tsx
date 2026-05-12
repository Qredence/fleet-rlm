import { PanelRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { cn } from "@/lib/utils";
import { useNavigationStore } from "@/stores/navigation-store";

export function LayoutHeader() {
  const { activeNav, isCanvasOpen, toggleCanvas } = useNavigationStore();
  const isMobile = useIsMobile();

  const titleMap: Record<string, string> = {
    workspace: "Workbench",
    volumes: "Volumes",
    optimization: "Optimization",
    settings: "Settings",
    history: "History",
  };
  const title = titleMap[activeNav] || "Dashboard";
  const canvasActionLabel = isCanvasOpen ? "Hide panel" : "Show panel";

  return (
    <header
      className={cn(
        "flex shrink-0 items-center justify-between gap-3 border-b border-border-subtle bg-background/95 backdrop-blur-sm",
        isMobile ? "px-3 py-2 pt-[max(env(safe-area-inset-top,0px),0.5rem)]" : "px-5 py-2",
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <SidebarTrigger className={isMobile ? "size-9 rounded-xl" : "size-8"} />
        <div className="min-w-0 truncate text-sm font-medium text-foreground">{title}</div>
      </div>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="icon"
            variant={isCanvasOpen ? "secondary" : "outline"}
            aria-label={canvasActionLabel}
            className={cn(
              "rounded-xl border-border-subtle/80",
              isMobile ? "size-9" : "size-8",
            )}
            onClick={toggleCanvas}
          >
            <PanelRight className="size-4" />
            <span className="sr-only">{canvasActionLabel}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="text-xs">
          {canvasActionLabel}
        </TooltipContent>
      </Tooltip>
    </header>
  );
}

export { LayoutHeader as ShellHeader };
