import type {
  ChatAttachmentItem,
  ChatInlineCitation,
  ChatRenderPart,
  ChatSourceItem,
} from "@/screens/workspace/use-workspace";
import type {
  AssistantTurnDisplayItem,
  AssistantTurnTracePartItem,
  ToolSessionItem,
  TraceDisplayItem,
} from "@/lib/workspace/chat-display-items";

type DirectExecutionPart = Extract<
  ChatRenderPart,
  {
    kind: "task" | "queue" | "tool" | "sandbox" | "environment_variables" | "status_note";
  }
>;

export interface CompactReasoning {
  key: string;
  label: string;
  text: string;
  duration?: number;
  isStreaming: boolean;
  runtimeBadges: string[];
}

export interface TrajectoryItem {
  id: string;
  index?: number;
  title: string;
  body: string;
  details?: string[];
  status: "pending" | "running" | "completed" | "failed";
  runtimeBadges: string[];
  source: "cot" | "reasoning" | "final_reasoning";
}

export interface ExecutionHighlight {
  id: string;
  label: string;
  summary: string;
  status: "pending" | "running" | "completed" | "failed";
  runtimeBadges: string[];
  count?: number;
}

type DirectExecutionSection = {
  [K in DirectExecutionPart["kind"]]: {
    id: string;
    kind: K;
    label: string;
    summary: string;
    defaultOpen: boolean;
    runtimeBadges: string[];
    part: Extract<DirectExecutionPart, { kind: K }>;
  };
}[DirectExecutionPart["kind"]];

export type ExecutionSection =
  | {
      id: string;
      kind: "tool_session";
      label: string;
      summary: string;
      defaultOpen: boolean;
      runtimeBadges: string[];
      session: Extract<TraceDisplayItem, { kind: "tool_session" }>;
    }
  | DirectExecutionSection;

export interface AssistantContentModel {
  item: AssistantTurnDisplayItem;
  answer: {
    text: string;
    hasContent: boolean;
    showStreamingShell: boolean;
  };
  trajectory: {
    overview?: CompactReasoning;
    items: TrajectoryItem[];
    displayMode: "hidden" | "compact" | "timeline";
    hasContent: boolean;
  };
  execution: {
    sections: ExecutionSection[];
    highlights: ExecutionHighlight[];
    hasContent: boolean;
    hasChatHighlights: boolean;
    toolSessionCount: number;
    sandboxActive: boolean;
  };
  evidence: {
    citations: ChatInlineCitation[];
    sources: ChatSourceItem[];
    attachments: ChatAttachmentItem[];
    hasContent: boolean;
  };
  summary: {
    show: boolean;
    trajectoryCount: number;
    toolSessionCount: number;
    sourceCount: number;
    attachmentCount: number;
    sandboxActive: boolean;
    runtimeBadges: string[];
  };
  complexity: "simple" | "medium" | "complex";
  supplementalParts: Exclude<ChatRenderPart, { kind: "reasoning" | "chain_of_thought" }>[];
  attachedToolSessions: Array<Extract<TraceDisplayItem, { kind: "tool_session" }>>;
  attachedTraceParts: AssistantTurnTracePartItem[];
  directExecutionParts: DirectExecutionPart[];
}

export type { DirectExecutionPart, ToolSessionItem };
