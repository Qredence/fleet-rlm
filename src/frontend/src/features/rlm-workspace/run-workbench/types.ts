import type { ChatAttachmentItem, ChatSourceItem } from "@/lib/data/types";
import type { WsServerMessage } from "@/lib/rlm-api";

export type RunStatus =
  | "idle"
  | "bootstrapping"
  | "cancelling"
  | "running"
  | "completed"
  | "error"
  | "cancelled";

export type DetailTab =
  | "iterations"
  | "evidence"
  | "callbacks"
  | "prompts"
  | "final";

export interface PromptHandleSummary {
  handleId: string;
  kind?: string;
  label?: string;
  path?: string;
  charCount?: number;
  lineCount?: number;
  preview?: string;
}

export interface ArtifactSummary {
  kind?: string;
  value?: unknown;
  variableName?: string;
  finalizationMode?: string;
  textPreview?: string;
}

export interface ContextSourceSummary {
  sourceId: string;
  kind: string;
  hostPath: string;
  stagedPath?: string;
  sourceType?: string;
  extractionMethod?: string;
  fileCount?: number;
  skippedCount?: number;
  warnings?: string[];
}

export interface IterationSummary {
  id: string;
  iteration: number;
  status: "pending" | "running" | "completed" | "error";
  phase?: string;
  summary: string;
  reasoningSummary?: string;
  code?: string;
  stdout?: string;
  stderr?: string;
  error?: string | null;
  durationMs?: number;
  callbackCount?: number;
  finalized?: boolean;
}

export interface CallbackSourceSummary {
  kind?: string;
  sourceId?: string;
  path?: string;
  startLine?: number;
  endLine?: number;
  chunkIndex?: number;
  header?: string;
  pattern?: string;
  preview?: string;
}

export interface CallbackSummary {
  id: string;
  callbackName: string;
  iteration?: number;
  status: string;
  task: string;
  label?: string;
  resultPreview?: string;
  source?: CallbackSourceSummary;
}

export interface ActivityEntry {
  id: string;
  kind: string;
  text: string;
  timestamp?: string;
  iteration?: number;
  phase?: string;
  status?: string;
  durationMs?: number;
  callbackCount?: number;
  warning?: string;
}

export interface RunSummary {
  durationMs?: number;
  sandboxesUsed?: number;
  terminationReason?: string;
  error?: string | null;
  warnings?: string[];
}

export interface RunWorkbenchState {
  status: RunStatus;
  runId?: string;
  repoUrl?: string;
  repoRef?: string | null;
  daytonaMode?: string;
  task?: string;
  contextSources: ContextSourceSummary[];
  iterations: IterationSummary[];
  callbacks: CallbackSummary[];
  promptHandles: PromptHandleSummary[];
  sources: ChatSourceItem[];
  attachments: ChatAttachmentItem[];
  activity: ActivityEntry[];
  selectedIterationId?: string | null;
  selectedCallbackId?: string | null;
  selectedTab: DetailTab;
  finalArtifact?: ArtifactSummary | null;
  summary?: RunSummary;
  errorMessage?: string | null;
  lastFrame?: WsServerMessage | null;
}
