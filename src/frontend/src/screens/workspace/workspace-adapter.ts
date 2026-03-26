// This module is the adapter boundary for the workspace runtime/store layer.
// It keeps the main hook focused on orchestration instead of adapter wiring.
export { applyWsFrameToArtifacts } from "@/lib/workspace/backend-artifact-event-adapter";
export { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
export { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
