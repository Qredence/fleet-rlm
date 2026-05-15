import { describe, expect, it } from "vitest";
import type { SessionListItem } from "@/lib/rlm-api/sessions";
import type { Conversation } from "@/lib/workspace/workspace-types";
import {
  isPlaceholderSessionTitle,
  shouldPreferLocalHistory,
  sortConversationsByUpdatedAt,
} from "../history-source";

function buildConversation(id: string, title: string, updatedAt: string): Conversation {
  return {
    id,
    title,
    messages: [],
    phase: "complete",
    createdAt: updatedAt,
    updatedAt,
  };
}

function buildSession(
  title: string,
  externalSessionId: string | null,
  updatedAt = "2026-05-15T18:00:00.000Z",
): SessionListItem {
  return {
    id: crypto.randomUUID(),
    title,
    status: "active",
    model_name: null,
    external_session_id: externalSessionId,
    created_at: updatedAt,
    updated_at: updatedAt,
  };
}

describe("history-source", () => {
  it("recognizes placeholder session titles", () => {
    expect(isPlaceholderSessionTitle("Chat session", null)).toBe(true);
    expect(
      isPlaceholderSessionTitle(
        "40122f3a-41d0-453d-8b60-61caba6fe37b",
        "40122f3a-41d0-453d-8b60-61caba6fe37b",
      ),
    ).toBe(true);
    expect(isPlaceholderSessionTitle("Session 42", null)).toBe(true);
    expect(isPlaceholderSessionTitle("Investigate history list", null)).toBe(false);
  });

  it("sorts conversations by recency", () => {
    const conversations = sortConversationsByUpdatedAt([
      buildConversation("older", "Older", "2026-05-15T10:00:00.000Z"),
      buildConversation("newer", "Newer", "2026-05-15T12:00:00.000Z"),
    ]);
    expect(conversations.map((conversation) => conversation.id)).toEqual(["newer", "older"]);
  });

  it("prefers local history when api sessions are placeholders", () => {
    const apiSessions = [
      buildSession("40122f3a-41d0-453d-8b60-61caba6fe37b", "40122f3a-41d0-453d-8b60-61caba6fe37b"),
      buildSession("d6c03c3b-33df-4145-ad00-df0ca03de083", "d6c03c3b-33df-4145-ad00-df0ca03de083"),
    ];
    const localConversations = [
      buildConversation("conv-1", "Use the available workspace tools to inspect the project", "2026-05-15T18:00:00.000Z"),
    ];

    expect(shouldPreferLocalHistory(apiSessions, localConversations)).toBe(true);
    expect(shouldPreferLocalHistory([buildSession("Actual chat title", null)], localConversations)).toBe(
      false,
    );
  });
});
