import {
  type Conversation,
  useConversations,
} from "@/screens/workspace/chat-history-store";
import { useWorkspaceUiStore } from "@/screens/workspace/workspace-ui-store";

function useWorkspaceShellHistory(): Conversation[] {
  return useConversations();
}

function useWorkspaceShellActions() {
  const newSession = useWorkspaceUiStore((state) => state.newSession);
  const requestConversationLoad = useWorkspaceUiStore(
    (state) => state.requestConversationLoad,
  );

  return {
    newSession,
    requestConversationLoad,
  };
}

export { useWorkspaceShellActions, useWorkspaceShellHistory };
export type { Conversation };
