import type { WsServerMessage } from "@/lib/rlm-api";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/ws-types";

export type CreationPhase =
  | "idle"
  | "understanding"
  | "generating"
  | "validating"
  | "complete";

export type PromptFeature =
  | "library"
  | "contextMemory"
  | "capabilities"
  | "webSearch";

export type PromptMode = "auto" | "workspace" | "webSearch" | "cowork";

export type InspectorTab = "trajectory" | "execution" | "evidence" | "graph";

export interface ChatMessage {
  id: string;
  type:
    | "user"
    | "assistant"
    | "system"
    | "trace"
    | "hitl"
    | "clarification"
    | "reasoning"
    | "plan_update"
    | "rlm_executing"
    | "memory_update";
  content: string;
  traceSource?: "live" | "trajectory" | "summary";
  phase?: 1 | 2 | 3;
  renderParts?: ChatRenderPart[];
  streaming?: boolean;
  hitlData?: {
    question: string;
    actions: { label: string; variant: "primary" | "secondary" }[];
    resolved?: boolean;
    resolvedLabel?: string;
  };
  clarificationData?: {
    question: string;
    stepLabel: string;
    options: { id: string; label: string; description?: string }[];
    customOptionId: string;
    resolved?: boolean;
    resolvedAnswer?: string;
  };
  reasoningData?: {
    parts: { type: "text"; text: string }[];
    isThinking: boolean;
    duration?: number;
  };
}

export type ChatRenderToolState =
  | "input-streaming"
  | "running"
  | "output-available"
  | "output-error";

export interface ChatTraceStep {
  id: string;
  index?: number;
  label: string;
  status: "pending" | "active" | "complete" | "error";
  details?: string[];
}

export interface ChatQueueItem {
  id: string;
  label: string;
  description?: string;
  completed: boolean;
}

export interface ChatTaskItem {
  id: string;
  text: string;
  file?: {
    name: string;
  };
}

export interface ChatEnvVarItem {
  name: string;
  value: string;
  required?: boolean;
}

export interface ChatInlineCitation {
  number?: string;
  title: string;
  url: string;
  description?: string;
  quote?: string;
  sourceId?: string;
  anchorId?: string;
  startChar?: number;
  endChar?: number;
}

export type ChatSourceKind =
  | "web"
  | "file"
  | "artifact"
  | "tool_output"
  | "other";

export interface ChatSourceItem {
  sourceId: string;
  kind: ChatSourceKind;
  title: string;
  url?: string;
  canonicalUrl?: string;
  displayUrl?: string;
  description?: string;
  quote?: string;
}

export interface ChatAttachmentItem {
  attachmentId: string;
  name: string;
  url?: string;
  previewUrl?: string;
  mimeType?: string;
  mediaType?: string;
  sizeBytes?: number;
  kind?: string;
  description?: string;
}

export interface RuntimeContext {
  depth: number;
  maxDepth: number;
  executionProfile: string;
  sandboxActive: boolean;
  effectiveMaxIters: number;
  volumeName?: string;
  executionMode?: string;
  runtimeMode?: string;
  sandboxId?: string;
  workspacePath?: string;
  sandboxTransition?: string;
}

export type ChatRenderPart =
  | {
      kind: "reasoning";
      parts: { type: "text"; text: string }[];
      isStreaming: boolean;
      duration?: number;
      label?: string;
      runtimeContext?: RuntimeContext;
    }
  | {
      kind: "chain_of_thought";
      title?: string;
      steps: ChatTraceStep[];
      runtimeContext?: RuntimeContext;
    }
  | {
      kind: "queue";
      title: string;
      items: ChatQueueItem[];
    }
  | {
      kind: "task";
      title: string;
      status: "pending" | "in_progress" | "completed" | "error";
      items?: ChatTaskItem[];
    }
  | {
      kind: "tool";
      title: string;
      toolType: string;
      state: ChatRenderToolState;
      stepIndex?: number;
      input?: unknown;
      output?: unknown;
      errorText?: string;
      runtimeContext?: RuntimeContext;
    }
  | {
      kind: "sandbox";
      title: string;
      state: ChatRenderToolState;
      stepIndex?: number;
      code?: string;
      output?: string;
      errorText?: string;
      language?: string;
      runtimeContext?: RuntimeContext;
    }
  | {
      kind: "environment_variables";
      title?: string;
      variables: ChatEnvVarItem[];
    }
  | {
      kind: "confirmation";
      question: string;
      state: "approval-requested" | "approved" | "rejected";
      actions?: { label: string; variant: "primary" | "secondary" }[];
    }
  | {
      kind: "inline_citation_group";
      citations: ChatInlineCitation[];
    }
  | {
      kind: "sources";
      sources: ChatSourceItem[];
      title?: string;
    }
  | {
      kind: "attachments";
      attachments: ChatAttachmentItem[];
      variant?: "grid" | "inline" | "list";
    }
  | {
      kind: "status_note";
      text: string;
      tone?: "neutral" | "success" | "warning" | "error";
      toolName?: string;
      stepIndex?: number;
      runtimeContext?: RuntimeContext;
    };

export type ArtifactStepType = "llm" | "repl" | "tool" | "memory" | "output";

export type ArtifactActorKind =
  | "root_rlm"
  | "sub_agent"
  | "delegate"
  | "unknown";

export interface ExecutionStep {
  id: string;
  parent_id?: string;
  sequence?: number;
  type: ArtifactStepType;
  label: string;
  depth?: number | null;
  actor_kind?: ArtifactActorKind | null;
  actor_id?: string | null;
  lane_key?: string | null;
  input?: unknown;
  output?: unknown;
  timestamp: number;
}

export interface ChatSubmitAttachment {
  id: string;
  name: string;
  mimeType: string;
  sizeBytes: number;
}

export interface ChatSubmitOptions {
  traceEnabled?: boolean;
  executionMode?: WsExecutionMode;
  runtimeMode?: WsRuntimeMode;
  repoUrl?: string;
  repoRef?: string;
  contextPaths?: string[];
  batchConcurrency?: number;
  attachments?: ChatSubmitAttachment[];
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  turnArtifactsByMessageId?: Record<string, ExecutionStep[]>;
  phase: CreationPhase;
  createdAt: string;
  updatedAt: string;
}

export interface ChatRuntime {
  messages: ChatMessage[];
  turnArtifactsByMessageId: Record<string, ExecutionStep[]>;
  inputValue: string;
  setInputValue: (value: string) => void;
  phase: CreationPhase;
  isTyping: boolean;
  handleSubmit: (options?: ChatSubmitOptions) => void;
  resolveHitl: (msgId: string, actionLabel: string) => void;
  resolveClarification: (msgId: string, answer: string) => void;
  loadConversation: (conversation: Conversation) => void;
}

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
  timestamp?: string | number;
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

export interface CompatBackfillInfo {
  eventId: string;
  runtimeMode?: string;
  usedSummary: boolean;
  usedFinalArtifact: boolean;
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
  compatBackfillCount: number;
  lastCompatBackfill?: CompatBackfillInfo | null;
}
