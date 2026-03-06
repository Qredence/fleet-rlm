import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import type { Conversation } from "@/hooks/useChatHistory";

export interface ChatSubmitAttachment {
  id: string;
  name: string;
  mimeType: string;
  sizeBytes: number;
}

export interface ChatSubmitOptions {
  traceEnabled?: boolean;
  attachments?: ChatSubmitAttachment[];
}

export interface ChatRuntime {
  messages: ChatMessage[];
  inputValue: string;
  setInputValue: (value: string) => void;
  phase: CreationPhase;
  isTyping: boolean;
  handleSubmit: (options?: ChatSubmitOptions) => void;
  resolveHitl: (msgId: string, actionLabel: string) => void;
  resolveClarification: (msgId: string, answer: string) => void;
  loadConversation: (conversation: Conversation) => void;
}
