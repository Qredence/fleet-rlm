import { PanelRightClose, PanelRightOpen } from "lucide-react";

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

function WorkspaceSidepanelBody({ isMobile }: { isMobile: boolean }) {
  const selectedTurn = useSelectedWorkspaceTurn();
  const traceState = useSessionTraceState();
  const activeTab = useWorkspaceUiStore((state) => state.activeSidepanelTab);
  const setTab = useWorkspaceUiStore((state) => state.setSidepanelTab);

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-background">
      <Tabs
        value={activeTab}
        onValueChange={(value) => setTab(value as WorkspaceSidepanelTab)}
        className="flex h-full min-h-0 flex-col gap-0 overflow-hidden"
      >
        <div className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border-subtle/70 px-3">
          <TabsList variant="line" className="gap-1 rounded-md bg-transparent p-0">
            {TABS.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="h-7 flex-none rounded-md px-3 typo-caption data-[active]:bg-sidebar-accent data-[active]:after:opacity-0 shadow-none"
              >
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
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
      <TooltipTrigger
        render={
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
        }
      />
      <TooltipContent side="bottom" className="text-xs">
        {open ? "Close panel" : "Open panel"}
      </TooltipContent>
    </Tooltip>
  );
}

export function WorkspaceSidepanel({ isMobile }: { isMobile: boolean }) {
  const open = useWorkspaceUiStore((state) => state.sidebarOpen);
  const setOpen = useWorkspaceUiStore((state) => state.setSidepanelOpen);

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="h-sheet-md gap-0 rounded-t-2xl border-x-0 border-b-0 p-0 sm:max-w-none"
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
              <WorkspaceSidepanelBody isMobile />
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
      {open ? <WorkspaceSidepanelBody isMobile={false} /> : null}
    </div>
  );
}
