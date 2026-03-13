import type { WsServerMessage } from "@/lib/rlm-api";

export type RunStatus =
  | "idle"
  | "bootstrapping"
  | "cancelling"
  | "running"
  | "completed"
  | "error"
  | "cancelled";

export type DetailTab = "timeline" | "prompts" | "node" | "final";

export interface PromptHandleSummary {
  handleId: string;
  kind?: string;
  label?: string;
  path?: string;
  charCount?: number;
  lineCount?: number;
  preview?: string;
}

export interface TaskSourceSummary {
  kind?: string;
  sourceId?: string;
  path?: string;
  startLine?: number;
  endLine?: number;
  preview?: string;
}

export interface RecursiveTaskSummary {
  task: string;
  label?: string;
  source?: TaskSourceSummary;
}

export interface ChildLinkSummary {
  childId?: string | null;
  callbackName: string;
  status: string;
  resultPreview: string;
  task: RecursiveTaskSummary;
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

export interface RunNode {
  nodeId: string;
  parentId?: string | null;
  depth: number;
  task: string;
  status: string;
  sandboxId?: string;
  workspacePath?: string;
  iterationCount?: number;
  error?: string | null;
  warnings?: string[];
  promptHandles: PromptHandleSummary[];
  childIds: string[];
  childLinks: ChildLinkSummary[];
  finalArtifact?: ArtifactSummary | null;
}

export interface TimelineEntry {
  id: string;
  kind: string;
  text: string;
  timestamp?: string;
  nodeId?: string;
  parentId?: string | null;
  depth?: number;
  sandboxId?: string;
  phase?: string;
  status?: string;
  promptHandleCount?: number;
  artifactPreview?: string;
  warning?: string;
  rawPayload?: Record<string, unknown>;
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
  rootId?: string;
  errorMessage?: string | null;
  nodes: Record<string, RunNode>;
  nodeOrder: string[];
  timeline: TimelineEntry[];
  selectedNodeId?: string | null;
  selectedTab: DetailTab;
  finalArtifact?: ArtifactSummary | null;
  summary?: RunSummary;
  lastFrame?: WsServerMessage | null;
}
