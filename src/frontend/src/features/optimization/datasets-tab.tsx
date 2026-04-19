import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, MessageSquare, Upload } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemContent,
  ItemTitle,
  ItemDescription,
  ItemActions,
  ItemGroup,
} from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { DataTable, type ColumnDef } from "@/components/product/data-table";
import { StateNotice } from "@/components/product";
import {
  useWorkspaceLayoutHistory,
  type Conversation,
} from "@/features/workspace/workspace-layout-contract";
import { RlmApiError } from "@/lib/rlm-api/client";
import { parseIsoTimestamp } from "@/lib/date";
import {
  datasetEndpoints,
  optimizationEndpoints,
  optimizationKeys,
  type DatasetResponse,
  type GEPAModuleInfo,
  type TranscriptTurnInput,
} from "@/lib/rlm-api/optimization";
import { sessionEndpoints, sessionKeys, type SessionListItem } from "@/lib/rlm-api/sessions";
import type { OptimizationRunDraft } from "@/features/optimization/optimization-form";

function formatDate(iso: string): string {
  return parseIsoTimestamp(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

const MODULE_SLUGS = [
  { value: "reflect-and-revise", label: "Reflect & Revise" },
  { value: "recursive-context-selection", label: "Recursive Context Selection" },
  { value: "recursive-decomposition", label: "Recursive Decomposition" },
  { value: "recursive-repair", label: "Recursive Repair" },
  { value: "recursive-verification", label: "Recursive Verification" },
] as const;
const EMPTY_MODULES: GEPAModuleInfo[] = [];

function sortConversations(conversations: Conversation[]) {
  return [...conversations].sort(
    (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
  );
}

function buildTranscriptTurns(conversation: Conversation): TranscriptTurnInput[] {
  const turns: TranscriptTurnInput[] = [];
  let pendingUserMessage: string | null = null;

  for (const message of conversation.messages) {
    if (message.type === "user") {
      pendingUserMessage = message.content;
      continue;
    }

    if (message.type === "assistant" && pendingUserMessage) {
      turns.push({
        user_message: pendingUserMessage,
        assistant_message: message.content,
      });
      pendingUserMessage = null;
    }
  }

  return turns;
}

function SessionRow({
  session,
  conversation,
  onPrepareRun,
  moduleProgramSpecsBySlug,
}: {
  session: SessionListItem;
  conversation?: Conversation;
  onPrepareRun?: (draft: OptimizationRunDraft) => void;
  moduleProgramSpecsBySlug: Map<string, string>;
}) {
  const queryClient = useQueryClient();
  const [selectedModule, setSelectedModule] = useState<string>("");

  const optimizeMutation = useMutation({
    mutationFn: async ({
      sessionId,
      moduleSlug,
      conversationTitle,
      transcriptTurns,
    }: {
      sessionId?: string;
      moduleSlug: string;
      conversationTitle?: string;
      transcriptTurns?: TranscriptTurnInput[];
    }) => {
      return typeof sessionId === "string"
        ? await sessionEndpoints.exportSession(sessionId, moduleSlug)
        : await datasetEndpoints.createFromTranscript({
            module_slug: moduleSlug,
            title: conversationTitle,
            turns: transcriptTurns ?? [],
          });
    },
    onSuccess: (dataset, variables) => {
      toast.success("Dataset ready for GEPA", {
        description: `Using dataset "${dataset.name}" — review the run settings before starting.`,
      });
      queryClient.invalidateQueries({ queryKey: optimizationKeys.datasets() });
      setSelectedModule("");
      const moduleSlug = variables.moduleSlug;
      const draft: OptimizationRunDraft = {
        datasetId: dataset.id,
        datasetName: dataset.name,
        moduleSlug,
        auto: "light",
        trainRatio: 0.8,
      };
      const programSpec = moduleProgramSpecsBySlug.get(moduleSlug);
      if (programSpec) {
        draft.programSpec = programSpec;
      }
      onPrepareRun?.(draft);
    },
    onError: (error) => {
      toast.error("GEPA dataset preparation failed", {
        description: error instanceof Error ? error.message : "Unexpected error",
      });
    },
  });

  const transcriptTurns = conversation ? buildTranscriptTurns(conversation) : undefined;

  return (
    <Item variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{session.title}</ItemTitle>
        <ItemDescription>{formatDate(session.created_at)}</ItemDescription>
      </ItemContent>
      <ItemActions>
        {conversation ? <Badge variant="secondary">Local history</Badge> : null}
        <Select
          value={selectedModule}
          onValueChange={(v) => {
            if (v) setSelectedModule(v);
          }}
        >
          <SelectTrigger className="h-8 w-select-lg text-xs" aria-label="Pick module">
            <SelectValue placeholder="Select module…" />
          </SelectTrigger>
          <SelectContent>
            {MODULE_SLUGS.map((mod) => (
              <SelectItem key={mod.value} value={mod.value}>
                {mod.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          disabled={
            !selectedModule ||
            optimizeMutation.isPending ||
            (conversation ? transcriptTurns?.length === 0 : false)
          }
          onClick={() => {
            if (!selectedModule) return;
            optimizeMutation.mutate({
              sessionId: conversation ? undefined : session.id,
              moduleSlug: selectedModule,
              conversationTitle: conversation?.title ?? session.title,
              transcriptTurns,
            });
          }}
        >
          <FileText className="mr-1.5 size-3.5" />
          {optimizeMutation.isPending ? "Preparing…" : "Prepare GEPA Run"}
        </Button>
      </ItemActions>
    </Item>
  );
}

function SessionsSection({
  onPrepareRun,
  moduleProgramSpecsBySlug,
}: {
  onPrepareRun?: (draft: OptimizationRunDraft) => void;
  moduleProgramSpecsBySlug: Map<string, string>;
}) {
  const localConversations = useWorkspaceLayoutHistory();
  const listParams = { limit: 10 };

  const sessionsQuery = useQuery({
    queryKey: sessionKeys.list(listParams),
    queryFn: ({ signal }) => sessionEndpoints.listSessions(listParams, signal),
    staleTime: 30_000,
  });

  const sessions = sessionsQuery.data?.items ?? [];
  const fallbackSessions = sortConversations(localConversations).map((conversation) => ({
    conversation,
    session: {
      id: `local:${conversation.id}`,
      title: conversation.title,
      status: "local",
      model_name: null,
      external_session_id: null,
      created_at: conversation.createdAt,
      updated_at: conversation.updatedAt,
    } satisfies SessionListItem,
  }));
  const shouldUseLocalFallback =
    (!sessions.length && fallbackSessions.length > 0) ||
    (sessionsQuery.isError &&
      fallbackSessions.length > 0 &&
      sessionsQuery.error instanceof RlmApiError &&
      sessionsQuery.error.status === 404);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">From Sessions</h3>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => sessionsQuery.refetch()}
          disabled={sessionsQuery.isFetching}
        >
          {sessionsQuery.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {sessionsQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-14 w-full rounded-lg" />
          <Skeleton className="h-14 w-full rounded-lg" />
          <Skeleton className="h-14 w-full rounded-lg" />
        </div>
      ) : shouldUseLocalFallback ? (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">
            Showing local session history because the durable sessions API is unavailable.
          </p>
          <ItemGroup>
            {fallbackSessions.map(({ session, conversation }, index) => (
              <SessionRow
                key={`${session.title}-${index}`}
                session={session}
                conversation={conversation}
                onPrepareRun={onPrepareRun}
                moduleProgramSpecsBySlug={moduleProgramSpecsBySlug}
              />
            ))}
          </ItemGroup>
        </div>
      ) : sessionsQuery.isError ? (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Failed to load sessions:{" "}
              {sessionsQuery.error instanceof Error ? sessionsQuery.error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      ) : sessions.length === 0 ? (
        <StateNotice
          icon={<MessageSquare className="size-10 text-muted-foreground/40" />}
          title="No sessions yet"
          description="Start a conversation in the Workbench to create sessions you can export as training datasets."
        />
      ) : (
        <ItemGroup>
          {sessions.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              onPrepareRun={onPrepareRun}
              moduleProgramSpecsBySlug={moduleProgramSpecsBySlug}
            />
          ))}
        </ItemGroup>
      )}
    </div>
  );
}

export function DatasetsTab({
  onPrepareRun,
}: {
  onPrepareRun?: (draft: OptimizationRunDraft) => void;
} = {}) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [moduleFilter, setModuleFilter] = useState<string>("all");
  const [dragActive, setDragActive] = useState(false);

  const modulesQuery = useQuery({
    queryKey: optimizationKeys.modules(),
    queryFn: ({ signal }) => optimizationEndpoints.modules(signal),
    staleTime: 60_000,
  });

  const datasetsQuery = useQuery({
    queryKey: optimizationKeys.datasetList(
      moduleFilter !== "all" ? { module_slug: moduleFilter } : undefined,
    ),
    queryFn: ({ signal }) =>
      datasetEndpoints.list(
        moduleFilter !== "all" ? { module_slug: moduleFilter } : undefined,
        signal,
      ),
    staleTime: 15_000,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      datasetEndpoints.upload(file, moduleFilter !== "all" ? moduleFilter : undefined),
    onSuccess: (result) => {
      toast.success("Dataset uploaded", {
        description: `"${result.name}" — ${result.row_count} rows.`,
      });
      queryClient.invalidateQueries({ queryKey: optimizationKeys.datasets() });
    },
    onError: (error) => {
      toast.error("Upload failed", {
        description: error instanceof Error ? error.message : "Unexpected error",
      });
    },
  });

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files?.length) return;
      const file = files[0];
      if (!file) return;
      uploadMutation.mutate(file);
    },
    [uploadMutation],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const modules: GEPAModuleInfo[] = modulesQuery.data ?? EMPTY_MODULES;
  const datasets = datasetsQuery.data?.items ?? [];
  const moduleProgramSpecsBySlug = useMemo(
    () =>
      new Map(
        (modulesQuery.data ?? EMPTY_MODULES).map(
          (moduleInfo) => [moduleInfo.slug, moduleInfo.program_spec] as const,
        ),
      ),
    [modulesQuery.data],
  );
  const datasetColumns = useMemo<ColumnDef<DatasetResponse>[]>(
    () => [
      { header: "Name", accessor: "name", sortable: true },
      {
        header: "Rows",
        accessor: (row) => row.row_count.toLocaleString(),
        className: "text-right tabular-nums",
      },
      { header: "Format", accessor: "format" },
      {
        header: "Module",
        accessor: (row) =>
          row.module_slug ? (
            <Badge variant="secondary" className="font-mono text-xs">
              {row.module_slug}
            </Badge>
          ) : (
            <span className="text-muted-foreground">—</span>
          ),
      },
      {
        header: "Created",
        accessor: (row) => formatDate(row.created_at),
      },
      {
        header: "Action",
        accessor: (row) => (
          <Button
            variant="outline"
            size="sm"
            disabled={!onPrepareRun}
            onClick={() => {
              const draft: OptimizationRunDraft = {
                datasetId: row.id,
                datasetName: row.name,
                moduleSlug: row.module_slug ?? null,
                auto: "light",
                trainRatio: 0.8,
              };
              if (row.module_slug) {
                const programSpec = moduleProgramSpecsBySlug.get(row.module_slug);
                if (programSpec) {
                  draft.programSpec = programSpec;
                }
              }
              onPrepareRun?.(draft);
            }}
          >
            Use in Run
          </Button>
        ),
        className: "w-select-xl",
      },
    ],
    [moduleProgramSpecsBySlug, onPrepareRun],
  );
  const filterLabel =
    moduleFilter === "all"
      ? "All modules"
      : (modules.find((m) => m.slug === moduleFilter)?.label ?? moduleFilter);

  return (
    <div className="flex flex-col gap-6">
      {/* Upload zone */}
      <div
        className={`flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-8 transition-colors ${
          dragActive
            ? "border-primary bg-primary/5"
            : "border-border-subtle hover:border-primary/40"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
      >
        <Upload className="size-8 text-muted-foreground" />
        <div className="text-center">
          <p className="text-sm font-medium">
            {uploadMutation.isPending ? "Uploading…" : "Drop a JSONL file here"}
          </p>
          <p className="text-xs text-muted-foreground">or click to browse</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={uploadMutation.isPending}
          onClick={() => fileInputRef.current?.click()}
        >
          Choose file
        </Button>
        <Input
          ref={fileInputRef}
          type="file"
          accept=".json,.jsonl"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {/* Filter & list */}
      <div className="flex items-center justify-between gap-3">
        <Select
          value={moduleFilter}
          onValueChange={(v) => {
            if (v) setModuleFilter(v);
          }}
        >
          <SelectTrigger className="w-select-lg" aria-label="Filter by module">
            <SelectValue>{filterLabel}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All modules</SelectItem>
            {modules.map((mod) => (
              <SelectItem key={mod.slug} value={mod.slug}>
                {mod.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => datasetsQuery.refetch()}
          disabled={datasetsQuery.isFetching}
        >
          {datasetsQuery.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {datasetsQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full rounded-lg" />
          <Skeleton className="h-10 w-full rounded-lg" />
          <Skeleton className="h-10 w-full rounded-lg" />
        </div>
      ) : datasetsQuery.isError ? (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Failed to load datasets:{" "}
              {datasetsQuery.error instanceof Error ? datasetsQuery.error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      ) : (
        <DataTable
          columns={datasetColumns}
          data={datasets}
          pageSize={10}
          emptyMessage="No datasets uploaded yet."
          rowKey={(row) => row.id}
        />
      )}

      {/* Session export */}
      <SessionsSection
        onPrepareRun={onPrepareRun}
        moduleProgramSpecsBySlug={moduleProgramSpecsBySlug}
      />
    </div>
  );
}
