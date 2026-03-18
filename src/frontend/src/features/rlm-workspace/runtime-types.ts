import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import type { ExecutionStep } from "@/lib/data/artifactTypes";
import type { Conversation } from "@/stores/chatHistoryStore";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/wsTypes";

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
