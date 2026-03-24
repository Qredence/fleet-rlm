import { useEffect, useMemo, useState } from "react";
import { PanelRight } from "lucide-react";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { RunWorkbench } from "@/app/workspace/workbench/RunWorkbench";
import { MessageInspectorPanel } from "@/app/workspace/inspector/MessageInspectorPanel";
import { useRunWorkbenchStore, useWorkspaceUiStore } from "@/screens/workspace/use-workspace";

export function useWorkspaceCanvasTitle() {
  return "Canvas";
}

export function WorkspaceCanvasPanel() {
  const selectedAssistantTurnId = useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
  const runStatus = useRunWorkbenchStore((state) => state.status);
  const runActivityCount = useRunWorkbenchStore((state) => state.activity.length);
  const runIterationCount = useRunWorkbenchStore((state) => state.iterations.length);
  const runCallbackCount = useRunWorkbenchStore((state) => state.callbacks.length);
  const hasRunContent = useMemo(
    () =>
      runStatus !== "idle" || runActivityCount > 0 || runIterationCount > 0 || runCallbackCount > 0,
    [runActivityCount, runCallbackCount, runIterationCount, runStatus],
  );
  const [activeTab, setActiveTab] = useState<"turn" | "run">(
    hasRunContent && !selectedAssistantTurnId ? "run" : "turn",
  );

  useEffect(() => {
    if (selectedAssistantTurnId) {
      setActiveTab("turn");
      return;
    }
    if (hasRunContent) {
      setActiveTab("run");
    }
  }, [hasRunContent, selectedAssistantTurnId]);

  return (
    <Tabs
      value={hasRunContent ? activeTab : "turn"}
      onValueChange={(value) => setActiveTab(value as "turn" | "run")}
      className="flex h-full min-h-0 flex-col gap-0 overflow-hidden"
    >
      <div className="border-b border-border-subtle/70 px-3 py-2">
        <TabsList className="inline-flex rounded-lg border border-border-subtle/70 bg-muted/40 p-1">
          <TabsTrigger value="turn">Turn</TabsTrigger>
          {hasRunContent ? <TabsTrigger value="run">Run</TabsTrigger> : null}
        </TabsList>
      </div>

      <TabsContent value="turn" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <MessageInspectorPanel />
      </TabsContent>

      {hasRunContent ? (
        <TabsContent value="run" className="mt-0 min-h-0 flex-1 overflow-hidden px-3 py-3">
          <RunWorkbench />
        </TabsContent>
      ) : null}
    </Tabs>
  );
}

export function WorkspaceCanvasUnavailablePanel() {
  return (
    <Empty className="h-full rounded-none border-0 bg-transparent">
      <EmptyMedia variant="icon">
        <PanelRight />
      </EmptyMedia>
      <EmptyContent>
        <EmptyTitle>Workbench unavailable</EmptyTitle>
        <EmptyDescription>
          The Workbench requires a live FastAPI runtime. Disable VITE_MOCK_MODE to connect to the
          backend.
        </EmptyDescription>
      </EmptyContent>
    </Empty>
  );
}
