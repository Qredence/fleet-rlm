import { useEffect, useMemo } from "react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { buildChatDisplayItems } from "@/features/rlm-workspace/chatDisplayItems";
import { buildAssistantContentModel } from "@/features/rlm-workspace/assistant-content/buildAssistantContentModel";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/types";
import { useChatStore } from "@/stores/chatStore";
import { useNavigationStore } from "@/stores/navigationStore";
import type { InspectorTab } from "@/lib/data/types";
import type { ExecutionStep } from "@/stores/artifactStore";
import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";
import { executionSectionState, statusTone } from "./utils/inspector-utils";
import { renderBadges } from "./components/inspector-components";

import { TrajectoryInspectorTab } from "./tabs/TrajectoryInspectorTab";
import { ExecutionInspectorTab } from "./tabs/ExecutionInspectorTab";
import { EvidenceInspectorTab } from "./tabs/EvidenceInspectorTab";
import { GraphInspectorTab } from "./tabs/GraphInspectorTab";

type TabOption = {
  id: InspectorTab;
  label: string;
};

function EmptyInspectorState({
  hasAssistantTurns,
}: {
  hasAssistantTurns: boolean;
}) {
  return (
    <div className="flex h-full items-center justify-center px-4 py-6">
      <Card className="w-full max-w-md border-border-subtle/80 bg-card/75 shadow-none">
        <CardHeader>
          <CardTitle>Message Inspector</CardTitle>
          <CardDescription>
            {hasAssistantTurns
              ? "Select an assistant response in the chat to inspect its trajectory, execution details, evidence, and relationships."
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
  if (
    model.execution.sections.some(
      (section) => executionSectionState(section) === "failed",
    )
  ) {
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
    model.trajectory.items.some(
      (item) => item.status === "pending" || item.status === "running",
    ) ||
    model.trajectory.overview?.isStreaming
  ) {
    return "running";
  }
  return "completed";
}

export function MessageInspectorPanel() {
  const messages = useChatStore((state) => state.messages);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const turnArtifactsByMessageId = useChatStore(
    (state) => state.turnArtifactsByMessageId,
  );
  const {
    selectedAssistantTurnId,
    activeInspectorTab,
    setInspectorTab,
  } = useNavigationStore();

  const hasAssistantTurns = useMemo(
    () => messages.some((m) => m.type === "assistant"),
    [messages]
  );

  const selectedTurn = useMemo(
    () => {
      if (!selectedAssistantTurnId) return null;
      return buildChatDisplayItems(messages, {
        showPendingAssistantShell: isStreaming,
      }).find((item) => item.kind === "assistant_turn" && item.turnId === selectedAssistantTurnId) ?? null;
    },
    [isStreaming, messages, selectedAssistantTurnId],
  ) as Extract<ReturnType<typeof buildChatDisplayItems>[number], { kind: "assistant_turn" }> | null;

  const model = useMemo(
    () => (selectedTurn ? buildAssistantContentModel(selectedTurn) : null),
    [selectedTurn],
  );

  const graphSteps = useMemo(
    () => selectedTurn ? turnArtifactsByMessageId[selectedTurn.turnId] ?? [] : [],
    [selectedTurn, turnArtifactsByMessageId]
  );

  const showGraph = useMemo(() => hasMeaningfulGraph(graphSteps), [graphSteps]);

  const tabs = useMemo<TabOption[]>(() => {
    if (!model) return [];
    return [
      { id: "trajectory", label: "Trajectory" },
      ...(model.execution.hasContent
        ? ([{ id: "execution", label: "Execution" }] as TabOption[])
        : []),
      ...(model.evidence.hasContent
        ? ([{ id: "evidence", label: "Evidence" }] as TabOption[])
        : []),
      ...(showGraph ? ([{ id: "graph", label: "Graph" }] as TabOption[]) : []),
    ];
  }, [model, showGraph]);

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeInspectorTab)) {
      setInspectorTab("trajectory");
    }
  }, [activeInspectorTab, setInspectorTab, tabs]);

  if (!selectedTurn || !model) {
    return <EmptyInspectorState hasAssistantTurns={hasAssistantTurns} />;
  }

  const currentTab =
    tabs.find((tab) => tab.id === activeInspectorTab)?.id ?? "trajectory";
  const turnStatus = statusTone(selectedTurnStatus(model));
  const summaryBadges = [
    ...model.summary.runtimeBadges,
    ...(model.summary.sandboxActive ? ["sandbox active"] : []),
  ];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-4 py-2">
        <Card className="gap-0 rounded-xl border-border-subtle/80 bg-card/75 shadow-none">
          <CardHeader className="space-y-3 px-3 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="space-y-1">
                <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                  Message Inspector
                </p>
                <CardTitle className="text-sm font-medium text-foreground">
                  {selectedTurn.isPendingShell
                    ? "Assistant turn in progress"
                    : "Selected assistant turn"}
                </CardTitle>
              </div>
              <Badge variant={turnStatus.variant} className={inspectorStyles.badge.status}>
                {turnStatus.label}
              </Badge>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {model.summary.trajectoryCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.trajectoryCount} trajector
                  {model.summary.trajectoryCount === 1 ? "y" : "ies"}
                </Badge>
              ) : null}
              {model.summary.toolSessionCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.toolSessionCount} tool session
                  {model.summary.toolSessionCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {model.summary.sourceCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.sourceCount} source
                  {model.summary.sourceCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {model.summary.attachmentCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.attachmentCount} attachment
                  {model.summary.attachmentCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
            </div>

            {summaryBadges.length > 0 ? renderBadges(summaryBadges) : null}
          </CardHeader>
        </Card>
      </div>

      <Separator className="bg-border-subtle/70" />

      <Tabs
        value={currentTab}
        onValueChange={(value) => setInspectorTab(value as InspectorTab)}
        className="min-h-0 flex-1 gap-0"
      >
        <div className="px-4 py-2">
          <TabsList className="flex w-full rounded-xl border border-border-subtle/70 bg-card/70 p-1">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <Separator className="bg-border-subtle/70" />

        <TrajectoryInspectorTab model={model} />
        {model.execution.hasContent ? (
          <ExecutionInspectorTab model={model} />
        ) : null}
        {model.evidence.hasContent ? <EvidenceInspectorTab model={model} /> : null}
        {showGraph ? <GraphInspectorTab steps={graphSteps} /> : null}
      </Tabs>
    </div>
  );
}
