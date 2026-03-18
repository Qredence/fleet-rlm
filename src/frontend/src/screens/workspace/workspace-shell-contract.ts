import {
  useConversations,
  type Conversation,
} from "@/screens/workspace/model/chat-history-store";
import { useWorkspaceUiStore } from "@/screens/workspace/model/workspace-ui-store";

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
