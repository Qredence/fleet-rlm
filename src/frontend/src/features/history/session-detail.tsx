import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DetailDrawer } from "@/components/product/detail-drawer";
import { PropertyList, PropertyItem, PropertyGroup } from "@/components/product/property-list";
import { Skeleton } from "@/components/ui/skeleton";
import { StateNotice } from "@/components/product";
import { useWorkspaceLayoutHistory } from "@/features/workspace/workspace-layout-contract";
import { parseIsoTimestamp } from "@/lib/date";
import { sessionEndpoints, sessionKeys } from "@/lib/rlm-api/sessions";
import type { HistorySelection } from "./history-screen";
import { SessionReplay } from "./session-replay";

interface SessionDetailProps {
  selectedSession: HistorySelection;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function formatDate(iso: string): string {
  return parseIsoTimestamp(iso).toLocaleString();
}

export function SessionDetail({ selectedSession, open, onOpenChange }: SessionDetailProps) {
  const queryClient = useQueryClient();
  const localConversations = useWorkspaceLayoutHistory();
  const localConversation =
    selectedSession.source === "local"
      ? (localConversations.find(
          (conversation) => conversation.id === selectedSession.conversationId,
        ) ?? null)
      : null;
  const sessionId = selectedSession.source === "api" ? selectedSession.sessionId : null;

  const detailQuery = useQuery({
    queryKey: sessionId != null ? sessionKeys.detail(sessionId) : ["sessions", "detail", "local"],
    queryFn: ({ signal }) => sessionEndpoints.getSession(sessionId!, signal),
    enabled: open && sessionId != null,
  });

  const archiveMutation = useMutation({
    mutationFn: () => sessionEndpoints.deleteSession(sessionId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sessionKeys.all });
      onOpenChange(false);
    },
    onError: () => undefined,
  });

  const session = detailQuery.data;
  const localSession =
    localConversation != null
      ? {
          id: localConversation.id,
          title: localConversation.title,
          status: "local",
          external_session_id: null,
          workspace_id: "Local workspace history",
          model_name: null,
          turn_count: localConversation.messages.length,
          created_at: localConversation.createdAt,
          updated_at: localConversation.updatedAt,
        }
      : null;

  return (
    <DetailDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={session?.title ?? localSession?.title ?? "Session Detail"}
      description={session ? `Session #${session.id}` : localSession ? "Local session" : undefined}
      actions={
        sessionId != null && session?.status !== "archived" ? (
          <Button
            variant="destructive"
            size="sm"
            disabled={archiveMutation.isPending}
            onClick={() => archiveMutation.mutate()}
          >
            {archiveMutation.isPending ? "Archiving…" : "Archive"}
          </Button>
        ) : undefined
      }
    >
      {detailQuery.isLoading ? (
        <div className="flex flex-col gap-3 py-4">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : localSession ? (
        <div className="flex flex-col gap-6">
          <PropertyGroup title="Metadata">
            <PropertyList>
              <PropertyItem label="Status" value={localSession.status} />
              <PropertyItem label="Workspace" value={localSession.workspace_id} />
              <PropertyItem label="Model" value={localSession.model_name} />
              <PropertyItem label="Turns" value={String(localSession.turn_count)} />
              <PropertyItem label="Created" value={formatDate(localSession.created_at)} />
              <PropertyItem label="Updated" value={formatDate(localSession.updated_at)} />
            </PropertyList>
          </PropertyGroup>

          <PropertyGroup title="Transcript">
            <SessionReplay conversation={localConversation!} />
          </PropertyGroup>
        </div>
      ) : detailQuery.isError ? (
        <p className="py-4 text-sm text-destructive">Failed to load session details.</p>
      ) : session ? (
        <div className="flex flex-col gap-6">
          <PropertyGroup title="Metadata">
            <PropertyList>
              <PropertyItem label="Status" value={session.status} />
              <PropertyItem label="External ID" value={session.external_session_id} />
              <PropertyItem label="Workspace" value={session.workspace_id} />
              <PropertyItem label="Model" value={session.model_name} />
              <PropertyItem label="Turns" value={String(session.turn_count)} />
              <PropertyItem label="Created" value={formatDate(session.created_at)} />
              <PropertyItem label="Updated" value={formatDate(session.updated_at)} />
            </PropertyList>
          </PropertyGroup>

          <PropertyGroup title="Transcript">
            <SessionReplay sessionId={sessionId!} />
          </PropertyGroup>
        </div>
      ) : (
        <StateNotice
          icon={<MessageSquare className="size-10 text-muted-foreground/40" />}
          title="Select a session"
          description="Choose a session from the list to view its details"
        />
      )}
    </DetailDrawer>
  );
}
