import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Conversation } from "@/features/workspace/workspace-layout-contract";
import { sessionEndpoints, sessionKeys, type TurnItem } from "@/lib/rlm-api/sessions";

const PAGE_SIZE = 20;

function TurnBubble({ turn }: { turn: TurnItem }) {
  return (
    <div className="flex flex-col gap-2">
      {/* User message */}
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-primary/10 px-3 py-2">
          <p className="text-xs font-medium text-muted-foreground">User</p>
          <p className="whitespace-pre-wrap text-sm">{turn.user_message}</p>
        </div>
      </div>

      {/* Assistant message */}
      {turn.assistant_message ? (
        <div className="flex justify-start">
          <div className="max-w-[80%] rounded-lg bg-muted px-3 py-2">
            <p className="text-xs font-medium text-muted-foreground">Assistant</p>
            <p className="whitespace-pre-wrap text-sm">{turn.assistant_message}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LocalMessageBubble({
  role,
  content,
}: {
  role: "user" | "assistant" | "system";
  content: string;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] rounded-lg px-3 py-2 ${isUser ? "bg-primary/10" : "bg-muted"}`}>
        <p className="text-xs font-medium text-muted-foreground">
          {role === "user" ? "User" : role === "assistant" ? "Assistant" : "System"}
        </p>
        <p className="whitespace-pre-wrap text-sm">{content}</p>
      </div>
    </div>
  );
}

type SessionReplayProps =
  | { sessionId: string; conversation?: never }
  | { conversation: Conversation; sessionId?: never };

function SessionReplayApi({ sessionId }: { sessionId: string }) {
  const [offset, setOffset] = useState(0);
  const params = { limit: PAGE_SIZE, offset };

  const turnsQuery = useQuery({
    queryKey: sessionKeys.turns(sessionId, params),
    queryFn: ({ signal }) => sessionEndpoints.getSessionTurns(sessionId, params, signal),
  });

  if (turnsQuery.isLoading) {
    return (
      <div className="flex flex-col gap-3 py-4">
        <Skeleton className="h-16 w-3/4" />
        <Skeleton className="ml-auto h-16 w-3/4" />
        <Skeleton className="h-16 w-3/4" />
      </div>
    );
  }

  if (turnsQuery.isError) {
    return (
      <p className="py-4 text-sm text-destructive">
        Failed to load turns:{" "}
        {turnsQuery.error instanceof Error ? turnsQuery.error.message : "Unknown error"}
      </p>
    );
  }

  const data = turnsQuery.data;
  if (!data?.items.length) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No turns recorded for this session.
      </p>
    );
  }

  const hasPrev = offset > 0;
  const hasNext = data.has_more;

  return (
    <div className="flex flex-col gap-4 py-4">
      <div className="flex flex-col gap-3">
        {data.items.map((turn) => (
          <TurnBubble key={turn.id} turn={turn} />
        ))}
      </div>

      {hasPrev || hasNext ? (
        <div className="flex items-center justify-between pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!hasPrev}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            ← Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasNext}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next →
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function SessionReplayLocal({ conversation }: { conversation: Conversation }) {
  const messages = conversation.messages.filter(
    (message) =>
      (message.type === "user" || message.type === "assistant" || message.type === "system") &&
      message.content.trim().length > 0,
  );

  if (!messages.length) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No turns recorded for this session.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4 py-4">
      <div className="flex flex-col gap-3">
        {messages.map((message) => (
          <LocalMessageBubble
            key={message.id}
            role={message.type === "assistant" || message.type === "system" ? message.type : "user"}
            content={message.content}
          />
        ))}
      </div>
    </div>
  );
}

export function SessionReplay(props: SessionReplayProps) {
  return props.sessionId === undefined ? (
    <SessionReplayLocal conversation={props.conversation} />
  ) : (
    <SessionReplayApi sessionId={props.sessionId} />
  );
}
