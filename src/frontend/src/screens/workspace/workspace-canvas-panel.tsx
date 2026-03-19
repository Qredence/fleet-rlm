import { PanelRight } from "lucide-react";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

import { useChatStore } from "@/screens/workspace/model/chat-store";
import { RunWorkbench } from "@/screens/workspace/components/workbench/RunWorkbench";
import { MessageInspectorPanel } from "@/screens/workspace/components/inspector/MessageInspectorPanel";

export function useWorkspaceCanvasTitle() {
  const runtimeMode = useChatStore((state) => state.runtimeMode);

  return runtimeMode === "daytona_pilot" ? "Run Workbench" : "Message Inspector";
}

export function WorkspaceCanvasPanel() {
  const runtimeMode = useChatStore((state) => state.runtimeMode);

  return runtimeMode === "daytona_pilot" ? <RunWorkbench /> : <MessageInspectorPanel />;
}

export function WorkspaceCanvasUnavailablePanel() {
  return (
    <Empty className="h-full rounded-none border-0 bg-transparent">
      <EmptyMedia variant="icon">
        <PanelRight />
      </EmptyMedia>
      <EmptyContent>
        <EmptyTitle>RLM Workspace unavailable</EmptyTitle>
        <EmptyDescription>
          The RLM Workspace requires a live FastAPI runtime. Disable VITE_MOCK_MODE to connect to
          the backend.
        </EmptyDescription>
      </EmptyContent>
    </Empty>
  );
}
