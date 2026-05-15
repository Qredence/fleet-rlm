import type { SessionListItem } from "@/lib/rlm-api/sessions";
import type { Conversation } from "@/lib/workspace/workspace-types";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function sortConversationsByUpdatedAt(conversations: Conversation[]): Conversation[] {
  return [...conversations].sort(
    (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
  );
}

export function isPlaceholderSessionTitle(
  title: string | null | undefined,
  externalSessionId: string | null | undefined,
): boolean {
  const raw = (title ?? "").trim();
  if (!raw || raw === "Chat session") return true;
  if (externalSessionId && raw === externalSessionId) return true;
  if (UUID_PATTERN.test(raw)) return true;
  if (!raw.startsWith("Session ")) return false;
  const suffix = raw.slice("Session ".length).trim();
  return suffix.length > 0 && (/^\d+$/.test(suffix) || UUID_PATTERN.test(suffix));
}

export function shouldPreferLocalHistory(
  apiSessions: SessionListItem[],
  localConversations: Conversation[],
): boolean {
  return (
    localConversations.length > 0 &&
    apiSessions.length > 0 &&
    apiSessions.every((session) =>
      isPlaceholderSessionTitle(session.title, session.external_session_id),
    )
  );
}
