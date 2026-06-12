import { Database, GitBranch, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { cn } from "@/lib/utils";
import { useNavigationStore } from "@/stores/navigation-store";
import type { CanvasPanel } from "@/stores/navigation-types";

const PANEL_BUTTONS: { id: CanvasPanel; label: string; icon: typeof Terminal }[] = [
  { id: "workspace", label: "Workbench", icon: Terminal },
  { id: "graph", label: "Graph", icon: GitBranch },
  { id: "volumes", label: "Volumes", icon: Database },
];

export function LayoutHeader() {
  const { activeNav, isCanvasOpen, canvasPanel, openCanvasPanel } = useNavigationStore();
  const isMobile = useIsMobile();

  const titleMap: Record<string, string> = {
    workspace: "Workbench",
    volumes: "Volumes",
    settings: "Settings",
  };
  const title = titleMap[activeNav] || "Dashboard";

  const handlePanelButton = (id: CanvasPanel) => {
    if (isCanvasOpen && canvasPanel === id) {
      return; // already showing this panel
    }
    openCanvasPanel(id);
  };

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

      <div className="flex items-center gap-1">
        {activeNav === "workspace" ? (
          <div id="workspace-header-actions" className="flex items-center gap-1" />
        ) : null}
        {PANEL_BUTTONS.map(({ id, label, icon: Icon }) => (
          <Tooltip key={id}>
            <TooltipTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant={isCanvasOpen && canvasPanel === id ? "secondary" : "ghost"}
                aria-label={label}
                aria-pressed={isCanvasOpen && canvasPanel === id}
                className={cn("rounded-lg", isMobile ? "size-9" : "size-8")}
                onClick={() => handlePanelButton(id)}
              >
                <Icon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              {label}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </header>
  );
}

export { LayoutHeader as ShellHeader };
