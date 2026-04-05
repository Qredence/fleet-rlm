import { PanelRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { cn } from "@/lib/utils";
import { getLayoutPanelMeta } from "./panel-meta";
import { useNavigationStore } from "@/stores/navigation-store";

export function LayoutHeader() {
  const { activeNav, isCanvasOpen, toggleCanvas } = useNavigationStore();
  const isMobile = useIsMobile();

  const titleMap: Record<string, string> = {
    workspace: "Workbench",
    volumes: "Volumes",
    optimization: "Optimization",
    settings: "Settings",
  };
  const title = titleMap[activeNav] || "Dashboard";
  const panelMeta = getLayoutPanelMeta(activeNav);
  const canvasActionLabel = isCanvasOpen
    ? `Hide ${panelMeta.toggleLabel}`
    : `Show ${panelMeta.toggleLabel}`;
  const showCanvasToggle = activeNav !== "settings" && activeNav !== "optimization";

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

      {showCanvasToggle ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              size={isMobile ? "icon" : "sm"}
              variant={isCanvasOpen ? "secondary" : "outline"}
              aria-label={canvasActionLabel}
              className={cn(
                isMobile
                  ? "rounded-xl"
                  : "h-11 flex-shrink justify-start gap-3 rounded-2xl border-border-subtle/80 px-3.5 text-foreground/82 shadow-xs",
              )}
              onClick={toggleCanvas}
            >
              <PanelRight />
              {!isMobile ? (
                <span className="text-xs font-medium text-foreground/90">
                  {panelMeta.toggleLabel}
                </span>
              ) : null}
              <span className="sr-only">{canvasActionLabel}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <div className="flex flex-col gap-1">
              <p className="text-xs font-medium">{canvasActionLabel}</p>
              <p className="max-w-[14rem] text-[11px] leading-5 text-muted-foreground">
                {panelMeta.toggleDescription}
              </p>
            </div>
          </TooltipContent>
        </Tooltip>
      ) : null}
    </header>
  );
}

export { LayoutHeader as ShellHeader };
