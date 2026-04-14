import { useState, useDeferredValue, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageSquare, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Item, ItemContent, ItemTitle, ItemDescription } from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { StateNotice } from "@/components/product";
import { useWorkspaceLayoutHistory, type Conversation } from "@/features/workspace/workspace-layout-contract";
import { RlmApiError } from "@/lib/rlm-api/client";
import { cn } from "@/lib/utils";
import {
  sessionEndpoints,
  sessionKeys,
  type SessionListItem,
  type SessionListParams,
} from "@/lib/rlm-api/sessions";
import type { HistorySelection } from "./history-screen";

const PAGE_SIZE = 20;

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "active":
      return (
        <Badge variant="secondary" className="bg-success/15 text-success">
          Active
        </Badge>
      );
    case "archived":
      return <Badge variant="secondary">Archived</Badge>;
    case "local":
      return <Badge variant="secondary">Local</Badge>;
    default:
      return <Badge variant="secondary">{status}</Badge>;
  }
}

function formatRelativeTime(isoString: string): string {
  const normalized = isoString.endsWith("Z") ? isoString : `${isoString}Z`;
  const date = new Date(normalized);
  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffSec = Math.max(0, Math.floor(diffMs / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function SessionRow({
  session,
  isSelected,
  onSelect,
}: {
  session: SessionListItem;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <Item
      render={<button type="button" onClick={onSelect} />}
      variant="outline"
      size="sm"
      className={cn(
        "rounded-lg text-left hover:bg-muted/50",
        isSelected ? "border-primary/30 bg-muted/30" : "border-border-subtle",
      )}
    >
      <ItemContent>
        <ItemTitle>
          <span className="truncate">{session.title}</span>
          <StatusBadge status={session.status} />
        </ItemTitle>
        <ItemDescription className="text-xs">
          Created {formatRelativeTime(session.created_at)} ·{" "}
          Updated {formatRelativeTime(session.updated_at)}
        </ItemDescription>
      </ItemContent>
    </Item>
  );
}

interface SessionListProps {
  selectedSession: HistorySelection | null;
  onSelect: (session: HistorySelection | null) => void;
}

function sortConversations(conversations: Conversation[]) {
  return [...conversations].sort(
    (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
  );
}

function sessionErrorDetail(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unknown error";
}

export function SessionList({ selectedSession, onSelect }: SessionListProps) {
  const localConversations = useWorkspaceLayoutHistory();
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [statusFilter, setStatusFilter] = useState("all");
  const [offset, setOffset] = useState(0);

  const filterLabel =
    statusFilter === "all"
      ? "All"
      : statusFilter === "active"
        ? "Active"
        : "Archived";

  const params: SessionListParams = {
    search: deferredSearch || undefined,
    status: statusFilter !== "all" ? statusFilter : undefined,
    limit: PAGE_SIZE,
    offset,
  };

  const sessionsQuery = useQuery({
    queryKey: sessionKeys.list(params),
    queryFn: ({ signal }) => sessionEndpoints.listSessions(params, signal),
  });

  // Reset offset when filters change
  useResetOffset(deferredSearch, statusFilter, setOffset);

  const normalizedSearch = deferredSearch.trim().toLowerCase();
  const localItems = sortConversations(localConversations).filter((conversation) => {
    if (normalizedSearch && !conversation.title.toLowerCase().includes(normalizedSearch)) {
      return false;
    }
    return statusFilter !== "archived";
  });
  const shouldUseLocalFallback =
    (!sessionsQuery.data?.items.length && localItems.length > 0) ||
    (sessionsQuery.isError &&
      localItems.length > 0 &&
      sessionsQuery.error instanceof RlmApiError &&
      sessionsQuery.error.status === 404);

  const toolbar = (
    <div className="mb-4 flex items-center gap-3">
      <div className="relative max-w-xs flex-1">
        <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 pointer-events-none text-muted-foreground" />
        <Input
          placeholder="Search sessions…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-8"
        />
      </div>
      <Select
        value={statusFilter}
        onValueChange={(v) => {
          if (v) setStatusFilter(v);
        }}
      >
        <SelectTrigger className="w-[120px]" aria-label="Status filter">
          <SelectValue>{filterLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          <SelectItem value="active">Active</SelectItem>
          <SelectItem value="archived">Archived</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );

  if (sessionsQuery.isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {toolbar}
        <Skeleton className="h-16 w-full rounded-lg" />
        <Skeleton className="h-16 w-full rounded-lg" />
        <Skeleton className="h-16 w-full rounded-lg" />
      </div>
    );
  }

  if (sessionsQuery.isError) {
    if (shouldUseLocalFallback) {
      return (
        <div className="flex flex-col gap-2">
          {toolbar}
          {localItems.map((conversation) => {
            const isSelected =
              selectedSession?.source === "local" &&
              selectedSession.conversationId === conversation.id;
            return (
              <SessionRow
                key={conversation.id}
                session={{
                  id: Number.NaN,
                  title: conversation.title,
                  status: "local",
                  model_name: null,
                  external_session_id: null,
                  created_at: conversation.createdAt,
                  updated_at: conversation.updatedAt,
                }}
                isSelected={isSelected}
                onSelect={() =>
                  onSelect(
                    isSelected ? null : { source: "local", conversationId: conversation.id },
                  )
                }
              />
            );
          })}
        </div>
      );
    }
    return (
      <div>
        {toolbar}
        <p className="py-4 text-sm text-destructive">
          Failed to load sessions: {sessionErrorDetail(sessionsQuery.error)}
        </p>
      </div>
    );
  }

  if (shouldUseLocalFallback) {
    return (
      <div className="flex flex-col gap-2">
        {toolbar}
        {localItems.map((conversation) => {
          const isSelected =
            selectedSession?.source === "local" &&
            selectedSession.conversationId === conversation.id;
          return (
            <SessionRow
              key={conversation.id}
              session={{
                id: Number.NaN,
                title: conversation.title,
                status: "local",
                model_name: null,
                external_session_id: null,
                created_at: conversation.createdAt,
                updated_at: conversation.updatedAt,
              }}
              isSelected={isSelected}
              onSelect={() =>
                onSelect(isSelected ? null : { source: "local", conversationId: conversation.id })
              }
            />
          );
        })}
      </div>
    );
  }

  const data = sessionsQuery.data;
  if (!data?.items.length) {
    return (
      <div>
        {toolbar}
        {search ? (
          <StateNotice
            icon={<MessageSquare className="size-10 text-muted-foreground/40" />}
            title="No matching sessions"
            description="Try adjusting your search or filters."
          />
        ) : (
          <StateNotice
            icon={<MessageSquare className="size-10 text-muted-foreground/40" />}
            title="No sessions yet"
            description="Your conversation history will appear here"
            action={
              <Button variant="ghost" size="sm" onClick={() => window.location.assign("/app/workspace")}>
                Open Workbench
              </Button>
            }
          />
        )}
      </div>
    );
  }

  const hasPrev = offset > 0;
  const hasNext = data.has_more;

  return (
    <div className="flex flex-col gap-2">
      {toolbar}
      {data.items.map((session) => (
        <SessionRow
          key={session.id}
          session={session}
          isSelected={selectedSession?.source === "api" && selectedSession.sessionId === session.id}
          onSelect={() =>
            onSelect(
              selectedSession?.source === "api" && selectedSession.sessionId === session.id
                ? null
                : { source: "api", sessionId: session.id },
            )
          }
        />
      ))}

      {(hasPrev || hasNext) ? (
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

// ── Helpers ──────────────────────────────────────────────────────────────────

function useResetOffset(search: string, status: string, setOffset: (n: number) => void) {
  useEffect(() => {
    setOffset(0);
  }, [search, status, setOffset]);
}
