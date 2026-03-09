import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import type { Conversation } from "@/stores/chatHistoryStore";
import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import type { ExecutionStep } from "@/stores/artifactStore";

export interface ChatSubmitAttachment {
  id: string;
  name: string;
  mimeType: string;
  sizeBytes: number;
}

export interface ChatSubmitOptions {
  traceEnabled?: boolean;
  executionMode?: WsExecutionMode;
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
