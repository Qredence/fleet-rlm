import { BookOpen, Brain, Cpu, GitBranch } from "lucide-react";

import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";
import type { SidebarTab } from "@/lib/workspace/workspace-ui-store";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

interface SessionSidebarProps {
  messages: ChatMessage[];
}

const TABS: { id: SidebarTab; label: string; icon: React.ComponentType<{ className?: string }> }[] =
  [
    { id: "documents", label: "Documents", icon: BookOpen },
    { id: "memory", label: "Memory", icon: Brain },
    { id: "context", label: "Context", icon: Cpu },
    { id: "checkpoint", label: "Checkpoint", icon: GitBranch },
  ];

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
    // Also check raw content for status messages that reference paths
    if (msg.type === "trace" && msg.content) {
      const match = msg.content.match(/(?:document_path|loaded_path|path)[:=]\s*["']?([^\s"',]+)/);
      if (match?.[1]) seen.add(match[1]);
    }
  }
  return Array.from(seen);
}

function DocumentsTab({ messages }: { messages: ChatMessage[] }) {
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

function MemoryTab() {
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

function ContextTab() {
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
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70 font-medium">
            {label}
          </span>
          <span className="text-xs text-foreground font-mono break-all">{value}</span>
        </div>
      ))}
    </div>
  );
}

function CheckpointTab() {
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

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

export function SessionSidebar({ messages }: SessionSidebarProps) {
  const sidebarTab = useWorkspaceUiStore((s) => s.sidebarTab);
  const setSidebarTab = useWorkspaceUiStore((s) => s.setSidebarTab);

  return (
    <div
      data-slot="session-sidebar"
      className="flex flex-col h-full w-[280px] shrink-0 border-l border-border/60 bg-background/80"
    >
      {/* Tab bar */}
      <div className="flex items-center border-b border-border/60 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setSidebarTab(id)}
            className={cn(
              "flex flex-col items-center gap-0.5 flex-1 py-2.5 px-1 text-[10px] font-medium transition-colors",
              sidebarTab === id
                ? "text-foreground border-b-2 border-primary -mb-px"
                : "text-muted-foreground hover:text-foreground",
            )}
            aria-selected={sidebarTab === id}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <ScrollArea className="flex-1 min-h-0">
        {sidebarTab === "documents" && <DocumentsTab messages={messages} />}
        {sidebarTab === "memory" && <MemoryTab />}
        {sidebarTab === "context" && <ContextTab />}
        {sidebarTab === "checkpoint" && <CheckpointTab />}
      </ScrollArea>
    </div>
  );
}
