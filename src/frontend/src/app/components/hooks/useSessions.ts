/**
 * React Query hooks for session management.
 *
 * Sessions map to chat conversations in the skill creation flow.
 * In mock mode, sessions are managed via the mockStateStore.
 *
 * @example
 * ```tsx
 * const { sessions, createSession, isLoading } = useSessions();
 * const { mutateAsync: create } = createSession;
 * const newSession = await create({ title: 'Test Gen Skill' });
 * ```
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { isMockMode } from "../../lib/api/config";
import { sessionEndpoints } from "../../lib/api/endpoints";
import { createLocalId } from "../../lib/id";
import { useMockStateStore, type MockSession } from "../../stores/mockStateStore";

// ── Types ───────────────────────────────────────────────────────────

export interface Session {
  id: string;
  userId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  status: "active" | "completed" | "archived";
  metadata?: Record<string, unknown>;
}

// ── Query Keys ──────────────────────────────────────────────────────

export const sessionKeys = {
  all: ["sessions"] as const,
  list: () => [...sessionKeys.all, "list"] as const,
  detail: (id: string) => [...sessionKeys.all, "detail", id] as const,
};

function mockDelay(ms = 400): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asMetadata(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function toSession(
  raw: Record<string, unknown>,
  fallbackTitle: string,
): Session {
  const statusRaw = asString(raw.status);
  const status: Session["status"] =
    statusRaw === "active" ||
    statusRaw === "completed" ||
    statusRaw === "archived"
      ? statusRaw
      : "active";

  const now = new Date().toISOString();

  return {
    id: asString(raw.id) ?? "",
    userId: asString(raw.userId) ?? "",
    title: asString(raw.title) || fallbackTitle,
    createdAt: asString(raw.createdAt) || now,
    updatedAt: asString(raw.updatedAt) || now,
    status,
    metadata: asMetadata(raw.metadata),
  };
}

// ── useSessions ─────────────────────────────────────────────────────

interface UseSessionsReturn {
  sessions: Session[];
  isLoading: boolean;
  error: Error | null;
}

function mockSessionToSession(mock: MockSession): Session {
  return mock;
}

export function useSessions(): UseSessionsReturn {
  const mock = isMockMode();
  const { sessions: mockSessions } = useMockStateStore();

  const query = useQuery({
    queryKey: sessionKeys.list(),
    queryFn: async ({ signal }) => {
      if (mock) return mockSessions.map(mockSessionToSession);

      const response = await sessionEndpoints.list(signal);
      return (response || []).map((session) =>
        toSession(session, "Untitled Session"),
      );
    },
    staleTime: mock ? Infinity : undefined,
  });

  return {
    sessions: query.data ?? [],
    isLoading: query.isLoading,
    error: query.error,
  };
}

// ── useCreateSession ────────────────────────────────────────────────

export function useCreateSession() {
  const queryClient = useQueryClient();
  const mock = isMockMode();
  const { addSession } = useMockStateStore();

  return useMutation({
    mutationFn: async (input?: {
      title?: string;
      metadata?: Record<string, unknown>;
    }): Promise<Session> => {
      if (mock) {
        await mockDelay();
        const newSession: MockSession = {
          id: createLocalId("session"),
          userId: "usr_01",
          title: input?.title || "New Session",
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          status: "active",
          metadata: input?.metadata,
        };
        addSession(newSession);
        return newSession;
      }

      const response = await sessionEndpoints.create(input);
      return toSession(response, "New Session");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sessionKeys.all });
    },
  });
}
