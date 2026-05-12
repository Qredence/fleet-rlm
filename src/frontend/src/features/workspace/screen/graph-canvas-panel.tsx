import { useMemo } from "react";
import { EmptyPanel } from "@/components/product/empty-panel";
import { ScrollArea } from "@/components/ui/scroll-area";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import {
  GraphInspectorContent,
  hasMeaningfulGraph,
} from "@/features/workspace/inspection/tabs/graph-inspector-content";
import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
import {
  useChatStore,
  useWorkspaceUiStore,
} from "@/features/workspace/use-workspace";
import type { ExecutionStep } from "@/features/workspace/use-workspace";

export function GraphCanvasPanel() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const turnArtifactsByMessageId = useChatStore((s) => s.turnArtifactsByMessageId);
  const selectedAssistantTurnId = useWorkspaceUiStore((s) => s.selectedAssistantTurnId);

  const selectedTurn = useMemo(() => {
    if (!selectedAssistantTurnId) return null;
    return (
      buildChatDisplayItems(messages, { showPendingAssistantShell: isStreaming }).find(
        (item) => item.kind === "assistant_turn" && item.turnId === selectedAssistantTurnId,
      ) ?? null
    );
  }, [isStreaming, messages, selectedAssistantTurnId]) as Extract<
    ReturnType<typeof buildChatDisplayItems>[number],
    { kind: "assistant_turn" }
  > | null;

  const graphSteps = useMemo(
    () => (selectedTurn ? (turnArtifactsByMessageId[selectedTurn.turnId] ?? []) : []),
    [selectedTurn, turnArtifactsByMessageId],
  );

  const showGraph = useMemo(() => hasMeaningfulGraph(graphSteps), [graphSteps]);

  if (!showGraph) {
    return (
      <EmptyPanel
        title="No graph available"
        description="Select an assistant turn with meaningful execution structure to view its graph."
        className="h-full rounded-none border-0 bg-transparent"
      />
    );
  }

  return <GraphContent steps={graphSteps} />;
}

function GraphContent({ steps }: { steps: ExecutionStep[] }) {
  return (
    <ScrollArea className="h-full">
      <div className={inspectorStyles.tab.content}>
        <GraphInspectorContent steps={steps} />
      </div>
    </ScrollArea>
  );
}
