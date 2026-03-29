import {
  useChatHistoryStore,
  useConversations,
} from "@/lib/workspace/chat-history-store";
import type { Conversation } from "@/lib/workspace/workspace-types";
import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";

function useWorkspaceShellHistory(): Conversation[] {
  return useConversations();
}

function useWorkspaceShellActions() {
  const newSession = useWorkspaceUiStore((state) => state.newSession);
  const requestConversationLoad = useWorkspaceUiStore(
    (state) => state.requestConversationLoad,
  );
  const deleteConversation = useChatHistoryStore(
    (state) => state.deleteConversation,
  );
  const clearHistory = useChatHistoryStore((state) => state.clearHistory);

  return {
    newSession,
    requestConversationLoad,
    deleteConversation,
    clearHistory,
  };
}

export { useWorkspaceShellActions, useWorkspaceShellHistory };
export type { Conversation };
