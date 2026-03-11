import type { WsServerMessage } from "@/lib/rlm-api";

export type DaytonaRunStatus =
  | "idle"
  | "bootstrapping"
  | "cancelling"
  | "running"
  | "completed"
  | "error"
  | "cancelled";

export type DaytonaDetailTab = "prompts" | "node" | "final";

export interface DaytonaPromptHandleSummary {
  handleId: string;
  kind?: string;
  label?: string;
  path?: string;
  charCount?: number;
  lineCount?: number;
  preview?: string;
}

export interface DaytonaTaskSourceSummary {
  kind?: string;
  sourceId?: string;
  path?: string;
  startLine?: number;
  endLine?: number;
  preview?: string;
}

export interface DaytonaRecursiveTaskSummary {
  task: string;
  label?: string;
  source?: DaytonaTaskSourceSummary;
}

export interface DaytonaChildLinkSummary {
  childId?: string | null;
  callbackName: string;
  status: string;
  resultPreview: string;
  task: DaytonaRecursiveTaskSummary;
}

export interface DaytonaArtifactSummary {
  kind?: string;
  value?: unknown;
  variableName?: string;
  finalizationMode?: string;
  textPreview?: string;
}

export interface DaytonaRunNode {
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
  promptHandles: DaytonaPromptHandleSummary[];
  childIds: string[];
  childLinks: DaytonaChildLinkSummary[];
  finalArtifact?: DaytonaArtifactSummary | null;
}

export interface DaytonaTimelineEntry {
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

export interface DaytonaRunSummary {
  durationMs?: number;
  sandboxesUsed?: number;
  terminationReason?: string;
  error?: string | null;
  warnings?: string[];
}

export interface DaytonaWorkbenchStateData {
  status: DaytonaRunStatus;
  runId?: string;
  repoUrl?: string;
  repoRef?: string | null;
  task?: string;
  rootId?: string;
  errorMessage?: string | null;
  nodes: Record<string, DaytonaRunNode>;
  nodeOrder: string[];
  timeline: DaytonaTimelineEntry[];
  selectedNodeId?: string | null;
  selectedTab: DaytonaDetailTab;
  finalArtifact?: DaytonaArtifactSummary | null;
  summary?: DaytonaRunSummary;
  lastFrame?: WsServerMessage | null;
}
