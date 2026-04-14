import { useEffect, useMemo } from "react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
import {
  buildAssistantContentModel,
  type AssistantContentModel,
} from "@/features/workspace/ui/assistant-content/model";
import type { ExecutionStep } from "@/features/workspace/use-workspace";
import { useChatStore } from "@/features/workspace/use-workspace";
import { useWorkspaceUiStore } from "@/features/workspace/use-workspace";
import type { InspectorTab } from "@/features/workspace/use-workspace";
import { executionSectionState } from "./ui/inspector-ui";

import { ExecutionInspectorTab } from "./tabs/execution-inspector-tab";
import { GraphInspectorTab } from "./tabs/graph-inspector-tab";
import { MessageInspectorTab } from "./tabs/message-inspector-tab";

type TabOption = {
  id: InspectorTab;
  label: string;
};

function EmptyInspectorState({ hasAssistantTurns }: { hasAssistantTurns: boolean }) {
  return (
    <div className="flex h-full items-center justify-center px-4 py-6">
      <Card className="w-full max-w-md border-border-subtle/80 bg-card/75 shadow-none">
        <CardHeader>
          <CardTitle>Message Inspector</CardTitle>
          <CardDescription>
            {hasAssistantTurns
              ? "Select an assistant response in the chat to inspect its message details, execution details, and relationships."
              : "Send a message to populate the inspector with assistant-turn details."}
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}

function hasMeaningfulGraph(steps: ExecutionStep[]) {
  if (steps.length < 2) return false;

  const lanes = new Set(
    steps
      .map((step) => step.lane_key ?? `${step.actor_kind ?? "unknown"}:${step.actor_id ?? ""}`)
      .filter(Boolean),
  );
  if (lanes.size > 1) return true;

  if (steps.some((step) => step.actor_kind === "delegate" || step.actor_kind === "sub_agent")) {
    return true;
  }

  const childCounts = new Map<string, number>();
  for (const step of steps) {
    if (!step.parent_id) continue;
    childCounts.set(step.parent_id, (childCounts.get(step.parent_id) ?? 0) + 1);
  }

  return [...childCounts.values()].some((count) => count > 1);
}

function selectedTurnStatus(
  model: AssistantContentModel,
): "pending" | "running" | "completed" | "failed" {
  if (model.execution.sections.some((section) => executionSectionState(section) === "failed")) {
    return "failed";
  }
  if (model.trajectory.items.some((item) => item.status === "failed")) {
    return "failed";
  }
  if (
    model.answer.showStreamingShell ||
    model.execution.sections.some((section) => {
      const state = executionSectionState(section);
      return state === "pending" || state === "running";
    }) ||
    model.trajectory.items.some((item) => item.status === "pending" || item.status === "running") ||
    model.trajectory.overview?.isStreaming
  ) {
    return "running";
  }
  return "completed";
}

export function MessageInspectorPanel() {
  const messages = useChatStore((state) => state.messages);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const turnArtifactsByMessageId = useChatStore((state) => state.turnArtifactsByMessageId);
  const selectedAssistantTurnId = useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
  const activeInspectorTab = useWorkspaceUiStore((state) => state.activeInspectorTab);
  const setInspectorTab = useWorkspaceUiStore((state) => state.setInspectorTab);

  const hasAssistantTurns = useMemo(() => messages.some((m) => m.type === "assistant"), [messages]);

  const selectedTurn = useMemo(() => {
    if (!selectedAssistantTurnId) return null;
    return (
      buildChatDisplayItems(messages, {
        showPendingAssistantShell: isStreaming,
      }).find(
        (item) => item.kind === "assistant_turn" && item.turnId === selectedAssistantTurnId,
      ) ?? null
    );
  }, [isStreaming, messages, selectedAssistantTurnId]) as Extract<
    ReturnType<typeof buildChatDisplayItems>[number],
    { kind: "assistant_turn" }
  > | null;

  const model = useMemo(
    () => (selectedTurn ? buildAssistantContentModel(selectedTurn) : null),
    [selectedTurn],
  );

  const graphSteps = useMemo(
    () => (selectedTurn ? (turnArtifactsByMessageId[selectedTurn.turnId] ?? []) : []),
    [selectedTurn, turnArtifactsByMessageId],
  );

  const showGraph = useMemo(() => hasMeaningfulGraph(graphSteps), [graphSteps]);

  const tabs = useMemo<TabOption[]>(() => {
    if (!model) return [];
    return [
      { id: "message", label: "Message" },
      ...(model.execution.hasContent
        ? ([{ id: "execution", label: "Execution" }] as TabOption[])
        : []),
      ...(showGraph ? ([{ id: "graph", label: "Graph" }] as TabOption[]) : []),
    ];
  }, [model, showGraph]);

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeInspectorTab)) {
      setInspectorTab("message");
    }
  }, [activeInspectorTab, setInspectorTab, tabs]);

  if (!selectedTurn || !model) {
    return <EmptyInspectorState hasAssistantTurns={hasAssistantTurns} />;
  }

  const currentTab = tabs.find((tab) => tab.id === activeInspectorTab)?.id ?? "message";

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <Tabs
        value={currentTab}
        onValueChange={(value) => setInspectorTab(value as InspectorTab)}
        className="flex min-h-0 flex-1 flex-col gap-0 overflow-hidden"
      >
        <div className="px-4 py-2">
          <TabsList variant="default" className="border border-border-subtle/70 bg-muted/40">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <Separator className="bg-border-subtle/70" />

        <MessageInspectorTab model={model} status={selectedTurnStatus(model)} />
        {model.execution.hasContent ? <ExecutionInspectorTab model={model} /> : null}
        {showGraph ? <GraphInspectorTab steps={graphSteps} /> : null}
      </Tabs>
    </div>
  );
}
