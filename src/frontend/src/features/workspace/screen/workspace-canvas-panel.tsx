import { useEffect, useMemo } from "react";
import { BookOpen, Brain, Cpu } from "lucide-react";
import { EmptyPanel } from "@/components/product/empty-panel";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
import {
  buildAssistantContentModel,
  type AssistantContentModel,
} from "@/features/workspace/conversation/assistant-content/model";
import { executionSectionState } from "@/features/workspace/inspection/inspector-ui";
import { ExecutionInspectorTab } from "@/features/workspace/inspection/tabs/execution-inspector-tab";
import { GraphInspectorTab } from "@/features/workspace/inspection/tabs/graph-inspector-tab";
import { MessageInspectorTab } from "@/features/workspace/inspection/tabs/message-inspector-tab";
import { RunWorkbench } from "@/features/workspace/workbench/run-workbench";
import {
  useChatStore,
  useRunWorkbenchStore,
  useWorkspaceUiStore,
} from "@/features/workspace/use-workspace";
import type { ExecutionStep, InspectorTab } from "@/features/workspace/use-workspace";
import type { ChatMessage } from "@/lib/workspace/workspace-types";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

type TabOption = { id: InspectorTab; label: string };

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
  if (model.execution.sections.some((s) => executionSectionState(s) === "failed")) return "failed";
  if (model.trajectory.items.some((i) => i.status === "failed")) return "failed";
  if (
    model.answer.showStreamingShell ||
    model.execution.sections.some((s) => {
      const st = executionSectionState(s);
      return st === "pending" || st === "running";
    }) ||
    model.trajectory.items.some((i) => i.status === "pending" || i.status === "running") ||
    model.trajectory.overview?.isStreaming
  ) {
    return "running";
  }
  return "completed";
}

function extractTraceDocumentPath(content: string): string | null {
  const match = content.match(
    /(?:document_path|loaded_path|path)[:=]\s*(?:"([^"]+)"|'([^']+)'|(\S+))/,
  );
  return match?.[1] ?? match?.[2] ?? match?.[3] ?? null;
}

/* ------------------------------------------------------------------ */
/*  Session sidebar tab content                                       */
/* ------------------------------------------------------------------ */

function extractDocumentPaths(messages: ChatMessage[]): string[] {
  const seen = new Set<string>();
  for (const msg of messages) {
    if (!msg.renderParts) continue;
    for (const part of msg.renderParts) {
      if (part.kind === "tool" || part.kind === "sandbox") {
        const input = part.kind === "tool" ? part.input : undefined;
        if (input && typeof input === "object") {
          const rec = input as Record<string, unknown>;
          const path =
            typeof rec.document_path === "string"
              ? rec.document_path
              : typeof rec.loaded_path === "string"
                ? rec.loaded_path
                : typeof rec.path === "string"
                  ? rec.path
                  : null;
          if (path) seen.add(path);
        }
      }
    }
    if (msg.type === "trace" && msg.content) {
      const path = extractTraceDocumentPath(msg.content);
      if (path) seen.add(path);
    }
  }
  return Array.from(seen);
}

function DocumentsTabContent({ messages }: { messages: ChatMessage[] }) {
  const paths = extractDocumentPaths(messages);

  if (paths.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center">
        <BookOpen className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">No documents loaded</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 p-2">
      {paths.map((p) => (
        <div
          key={p}
          className="rounded-md px-3 py-2 text-xs text-foreground/80 bg-muted/50 font-mono break-all"
        >
          {p}
        </div>
      ))}
    </div>
  );
}

function MemoryTabContent() {
  const memoryEntries = useWorkspaceUiStore((s) => s.memoryEntries);

  if (memoryEntries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center">
        <Brain className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">No memory updates yet</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 p-2">
      {memoryEntries.map((entry) => (
        <div
          key={entry.id}
          className="rounded-lg border border-border/50 bg-card p-3 flex flex-col gap-1"
        >
          <p className="text-xs text-muted-foreground">{formatTimestamp(entry.timestamp)}</p>
          <p className="text-xs text-foreground leading-relaxed line-clamp-4">{entry.content}</p>
        </div>
      ))}
    </div>
  );
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

function ContextTabContent() {
  const runtimeContext = useWorkspaceUiStore((s) => s.runtimeContext);

  if (!runtimeContext) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center">
        <Cpu className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">No active runtime context</p>
      </div>
    );
  }

  const rows: { label: string; value: string | undefined }[] = [
    { label: "Volume", value: runtimeContext.volume_name },
    { label: "Workspace", value: runtimeContext.workspace_path },
    { label: "Sandbox ID", value: runtimeContext.sandbox_id },
    { label: "Execution mode", value: runtimeContext.execution_mode },
    { label: "Runtime mode", value: runtimeContext.runtime_mode },
    { label: "Profile", value: runtimeContext.execution_profile },
    {
      label: "Depth",
      value:
        runtimeContext.depth !== undefined
          ? `${runtimeContext.depth} / ${runtimeContext.max_depth}`
          : undefined,
    },
  ].filter((r): r is { label: string; value: string } => r.value != null && r.value !== "");

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center">
        <Cpu className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">No context fields available</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 p-2">
      {rows.map(({ label, value }) => (
        <div key={label} className="flex flex-col gap-0.5 rounded-md px-3 py-2 hover:bg-muted/40">
          <span className="typo-helper uppercase tracking-wider text-muted-foreground/70 font-medium">
            {label}
          </span>
          <span className="text-xs text-foreground font-mono break-all">{value}</span>
        </div>
      ))}
    </div>
  );
}

function CheckpointTabContent() {
  const pendingHitlMessageId = useWorkspaceUiStore((s) => s.pendingHitlMessageId);

  return (
    <div className="flex flex-col gap-3 p-3">
      <div
        className={cn(
          "rounded-lg border p-3 flex flex-col gap-1",
          pendingHitlMessageId
            ? "border-amber-500/30 bg-amber-500/5"
            : "border-border/50 bg-muted/30",
        )}
      >
        <div className="flex items-center gap-2">
          <div
            className={cn(
              "size-2 rounded-full",
              pendingHitlMessageId ? "bg-amber-500 animate-pulse" : "bg-muted-foreground/30",
            )}
          />
          <span
            className={cn(
              "text-xs font-medium",
              pendingHitlMessageId ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
            )}
          >
            {pendingHitlMessageId ? "Pending HITL approval" : "No pending approvals"}
          </span>
        </div>
        {pendingHitlMessageId && (
          <p className="text-xs text-muted-foreground pl-4">
            Message ID: <span className="font-mono">{pendingHitlMessageId}</span>
          </p>
        )}
      </div>

      <div className="rounded-lg border border-border/50 bg-card p-3">
        <p className="text-xs text-muted-foreground leading-relaxed">
          Checkpoints are created when the agent pauses for human review. Approve or reject via the
          approval modal that appears in the workspace.
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  WorkspaceCanvasPanel — single flat tab bar                        */
/* ------------------------------------------------------------------ */

export function WorkspaceCanvasPanel() {
  /* ---- chat / inspector state ---- */
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const turnArtifactsByMessageId = useChatStore((s) => s.turnArtifactsByMessageId);

  const selectedAssistantTurnId = useWorkspaceUiStore((s) => s.selectedAssistantTurnId);
  const activeInspectorTab = useWorkspaceUiStore((s) => s.activeInspectorTab);
  const setInspectorTab = useWorkspaceUiStore((s) => s.setInspectorTab);

  /* ---- run / workbench state ---- */
  const runStatus = useRunWorkbenchStore((s) => s.status);
  const runActivityCount = useRunWorkbenchStore((s) => s.activity.length);
  const runIterationCount = useRunWorkbenchStore((s) => s.iterations.length);
  const runCallbackCount = useRunWorkbenchStore((s) => s.callbacks.length);

  const hasRunContent = useMemo(
    () =>
      runStatus !== "idle" || runActivityCount > 0 || runIterationCount > 0 || runCallbackCount > 0,
    [runActivityCount, runCallbackCount, runIterationCount, runStatus],
  );

  /* ---- model building (lifted from MessageInspectorPanel) ---- */
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

  const model = useMemo(
    () => (selectedTurn ? buildAssistantContentModel(selectedTurn) : null),
    [selectedTurn],
  );

  const graphSteps = useMemo(
    () => (selectedTurn ? (turnArtifactsByMessageId[selectedTurn.turnId] ?? []) : []),
    [selectedTurn, turnArtifactsByMessageId],
  );

  const showGraph = useMemo(() => hasMeaningfulGraph(graphSteps), [graphSteps]);

  /* ---- dynamic tab list ---- */
  const tabs = useMemo<TabOption[]>(() => {
    const list: TabOption[] = [];

    if (model) {
      list.push({ id: "message", label: "Message" });
      if (model.execution.hasContent) list.push({ id: "execution", label: "Execution" });
      if (showGraph) list.push({ id: "graph", label: "Graph" });
    }

    if (hasRunContent) list.push({ id: "workbench", label: "Workbench" });

    // Session context tabs — always visible
    list.push({ id: "documents", label: "Docs" });
    list.push({ id: "memory", label: "Memory" });
    list.push({ id: "context", label: "Context" });
    list.push({ id: "checkpoint", label: "HITL" });

    return list;
  }, [hasRunContent, model, showGraph]);

  /* ---- invalid-tab recovery ---- */
  useEffect(() => {
    const first = tabs[0];
    if (tabs.length > 0 && first && !tabs.some((t) => t.id === activeInspectorTab)) {
      setInspectorTab(first.id);
    }
  }, [activeInspectorTab, setInspectorTab, tabs]);

  /* ---- empty state ---- */
  if (!model && !hasRunContent) {
    // Show session tabs even without inspector content
    const sessionTabs: TabOption[] = [
      { id: "documents", label: "Docs" },
      { id: "memory", label: "Memory" },
      { id: "context", label: "Context" },
      { id: "checkpoint", label: "HITL" },
    ];
    const sessionTab = sessionTabs.find((t) => t.id === activeInspectorTab)?.id ?? "documents";

    return (
      <Tabs
        value={sessionTab}
        onValueChange={(v) => setInspectorTab(v as InspectorTab)}
        className="flex h-full min-h-0 flex-col gap-0 overflow-hidden"
      >
        <div className="border-b border-border-subtle/70 px-3 py-2">
          <TabsList variant="default" className="border border-border-subtle/70 bg-muted/40">
            {sessionTabs.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <Separator className="bg-border-subtle/70" />
        <TabsContent value="documents" className="mt-0 min-h-0 flex-1 overflow-auto">
          <DocumentsTabContent messages={messages} />
        </TabsContent>
        <TabsContent value="memory" className="mt-0 min-h-0 flex-1 overflow-auto">
          <MemoryTabContent />
        </TabsContent>
        <TabsContent value="context" className="mt-0 min-h-0 flex-1 overflow-auto">
          <ContextTabContent />
        </TabsContent>
        <TabsContent value="checkpoint" className="mt-0 min-h-0 flex-1 overflow-auto">
          <CheckpointTabContent />
        </TabsContent>
      </Tabs>
    );
  }

  const currentTab = tabs.find((t) => t.id === activeInspectorTab)?.id ?? tabs[0]?.id ?? "message";

  return (
    <Tabs
      value={currentTab}
      onValueChange={(v) => setInspectorTab(v as InspectorTab)}
      className="flex h-full min-h-0 flex-col gap-0 overflow-hidden"
    >
      <div className="border-b border-border-subtle/70 px-3 py-2">
        <TabsList variant="default" className="border border-border-subtle/70 bg-muted/40">
          {tabs.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>

      <Separator className="bg-border-subtle/70" />

      {model ? (
        <>
          <MessageInspectorTab model={model} status={selectedTurnStatus(model)} />
          {model.execution.hasContent ? <ExecutionInspectorTab model={model} /> : null}
          {showGraph ? <GraphInspectorTab steps={graphSteps} /> : null}
        </>
      ) : null}

      {hasRunContent ? (
        <TabsContent value="workbench" className="mt-0 min-h-0 flex-1 overflow-hidden px-3 py-3">
          <RunWorkbench />
        </TabsContent>
      ) : null}

      {/* Session context tabs */}
      <TabsContent value="documents" className="mt-0 min-h-0 flex-1 overflow-auto">
        <DocumentsTabContent messages={messages} />
      </TabsContent>
      <TabsContent value="memory" className="mt-0 min-h-0 flex-1 overflow-auto">
        <MemoryTabContent />
      </TabsContent>
      <TabsContent value="context" className="mt-0 min-h-0 flex-1 overflow-auto">
        <ContextTabContent />
      </TabsContent>
      <TabsContent value="checkpoint" className="mt-0 min-h-0 flex-1 overflow-auto">
        <CheckpointTabContent />
      </TabsContent>
    </Tabs>
  );
}

export function WorkspaceCanvasUnavailablePanel() {
  return (
    <EmptyPanel
      title="Workbench unavailable"
      description="The Workbench requires a live FastAPI runtime. Disable VITE_MOCK_MODE to connect to the backend."
      className="h-full rounded-none border-0 bg-transparent"
    />
  );
}
