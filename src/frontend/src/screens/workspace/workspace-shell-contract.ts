import {
  type Conversation,
  useChatHistoryStore,
  useConversations,
} from "@/screens/workspace/chat-history-store";
import { useWorkspaceUiStore } from "@/screens/workspace/workspace-ui-store";

function useWorkspaceShellHistory(): Conversation[] {
  return useConversations();
}

function useWorkspaceShellActions() {
  const newSession = useWorkspaceUiStore((state) => state.newSession);
  const requestConversationLoad = useWorkspaceUiStore((state) => state.requestConversationLoad);
  const deleteConversation = useChatHistoryStore((state) => state.deleteConversation);
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
