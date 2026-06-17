import { PanelRightClose, PanelRightOpen, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  useWorkspaceUiStore,
  type WorkspaceSidepanelTab,
} from "@/features/workspace/use-workspace";
import { cn } from "@/lib/utils";

import { useSelectedWorkspaceTurn, useSessionTraceState } from "./use-session-trace";
import { TrajectoryTimeline } from "./tabs/trajectory-tab";
import { GraphTab } from "./tabs/graph-tab";
import { VolumeTab } from "./tabs/volume-tab";

const TABS = [
  { id: "trajectories", label: "Trajectories" },
  { id: "graph", label: "Graph" },
  { id: "volume", label: "Volume" },
] as const;

function WorkspaceSidepanelBody({ onClose, isMobile }: { onClose: () => void; isMobile: boolean }) {
  const selectedTurn = useSelectedWorkspaceTurn();
  const traceState = useSessionTraceState();
  const activeTab = useWorkspaceUiStore((state) => state.activeSidepanelTab);
  const setTab = useWorkspaceUiStore((state) => state.setSidepanelTab);

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-card/95">
      <Tabs
        value={activeTab}
        onValueChange={(value) => setTab(value as WorkspaceSidepanelTab)}
        className="flex h-full min-h-0 flex-col gap-0 overflow-hidden"
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border-subtle/70 px-3 py-2">
          <TabsList variant="line" className="gap-1 rounded-full bg-transparent p-0">
            {TABS.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="h-7 flex-none rounded-full px-3 text-xs data-[active]:bg-muted data-[active]:after:opacity-0"
              >
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="secondary"
                size="icon"
                className="size-8 shrink-0 rounded-full"
                aria-label="Close workspace sidepanel"
                onClick={onClose}
              >
                <X className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              Close panel
            </TooltipContent>
          </Tooltip>
        </div>

        <TabsContent value="trajectories" className="min-h-0 flex-1 overflow-hidden">
          <TrajectoryTimeline selectedTurn={selectedTurn} traceState={traceState} />
        </TabsContent>
        <TabsContent value="graph" className="min-h-0 flex-1 overflow-hidden">
          <GraphTab traceState={traceState} />
        </TabsContent>
        <TabsContent value="volume" className="min-h-0 flex-1 overflow-hidden">
          <VolumeTab isMobile={isMobile} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function WorkspaceSidepanelToggle() {
  const open = useWorkspaceUiStore((state) => state.sidebarOpen);
  const toggle = useWorkspaceUiStore((state) => state.toggleSidepanel);
  const Icon = open ? PanelRightClose : PanelRightOpen;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={open ? "secondary" : "ghost"}
          size="icon"
          className="size-8 rounded-lg"
          aria-label={open ? "Close workspace sidepanel" : "Open workspace sidepanel"}
          aria-pressed={open}
          onClick={toggle}
        >
          <Icon className="size-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="text-xs">
        {open ? "Close panel" : "Open panel"}
      </TooltipContent>
    </Tooltip>
  );
}

export function WorkspaceSidepanel({ isMobile }: { isMobile: boolean }) {
  const open = useWorkspaceUiStore((state) => state.sidebarOpen);
  const setOpen = useWorkspaceUiStore((state) => state.setSidepanelOpen);
  const close = useWorkspaceUiStore((state) => state.closeSidepanel);

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="h-sheet-md gap-0 rounded-t-3xl border-x-0 border-b-0 p-0 sm:max-w-none"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>Workspace sidepanel</SheetTitle>
            <SheetDescription>
              Inspect trajectories, graph, and Daytona volume files.
            </SheetDescription>
          </SheetHeader>
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center justify-center py-3">
              <div className="h-1.5 w-10 rounded-full bg-border" aria-hidden="true" />
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <WorkspaceSidepanelBody onClose={close} isMobile />
            </div>
          </div>
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <div
      data-workspace-sidepanel
      className={cn(
        "h-full min-h-0 w-full overflow-hidden transition-opacity duration-200",
        open ? "opacity-100" : "opacity-0",
      )}
      aria-hidden={!open}
    >
      {open ? <WorkspaceSidepanelBody onClose={close} isMobile={false} /> : null}
    </div>
  );
}
