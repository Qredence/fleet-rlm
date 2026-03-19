export type CreationPhase = "idle" | "understanding" | "generating" | "validating" | "complete";

export type PromptFeature = "library" | "contextMemory" | "capabilities" | "webSearch";

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

export type ChatSourceKind = "web" | "file" | "artifact" | "tool_output" | "other";

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
