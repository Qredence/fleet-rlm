import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { parseIsoTimestamp } from "@/lib/date";
import {
  optimizationEndpoints,
  optimizationKeys,
  type OptimizationRunSummary,
} from "@/lib/rlm-api/optimization";

const PHASE_LABELS: Record<string, string> = {
  loading: "Loading…",
  compiling: "Optimizing…",
  saving: "Saving…",
  done: "Complete",
  failed: "Failed",
};

function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase] ?? phase.charAt(0).toUpperCase() + phase.slice(1);
}

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "running":
      return (
        <Badge variant="secondary" className="animate-pulse bg-info/15 text-info">
          Running
        </Badge>
      );
    case "completed":
      return (
        <Badge variant="secondary" className="bg-success/15 text-success">
          Completed
        </Badge>
      );
    case "failed":
      return <Badge variant="destructive">Failed</Badge>;
    case "queued":
      return <Badge variant="secondary">Queued</Badge>;
    case "cancelled":
      return <Badge variant="outline">Cancelled</Badge>;
    default:
      return <Badge variant="secondary">{status}</Badge>;
  }
}

function ScoreBadge({ score }: { score: number }) {
  const colorClass =
    score >= 0.7 ? "bg-success/15 text-success" : score >= 0.4 ? "bg-warning/15 text-warning" : "";
  return (
    <Badge variant={score >= 0.7 ? "secondary" : "destructive"} className={colorClass}>
      {score.toFixed(4)}
    </Badge>
  );
}

function formatRelativeTime(isoString: string): string {
  const date = parseIsoTimestamp(isoString);
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

function formatDuration(startedAt: string, completedAt: string | null): string {
  if (!completedAt) return "—";
  const start = parseIsoTimestamp(startedAt).getTime();
  const end = parseIsoTimestamp(completedAt).getTime();
  const sec = Math.max(0, Math.floor((end - start) / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  return `${min}m ${remSec}s`;
}

function ResultRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-right text-xs font-medium">{children}</span>
    </div>
  );
}

function RunDetailPanel({ run }: { run: OptimizationRunSummary }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <CardTitle className="text-sm">
            Run #{run.id}
            {run.module_slug ? ` — ${run.module_slug}` : ""}
          </CardTitle>
          <StatusBadge status={run.status} />
        </div>
        {run.error ? (
          <CardDescription className="text-destructive">{run.error}</CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-0.5">
        <ResultRow label="Program">{run.program_spec}</ResultRow>
        <ResultRow label="Optimizer">{run.optimizer.toUpperCase()}</ResultRow>
        <ResultRow label="Intensity">{run.auto ?? "light"}</ResultRow>
        <ResultRow label="Train ratio">{(run.train_ratio * 100).toFixed(0)}%</ResultRow>
        {run.dataset_path ? (
          <ResultRow label="Dataset">
            <code className="text-xs">{run.dataset_path}</code>
          </ResultRow>
        ) : null}
        {run.train_examples != null ? (
          <ResultRow label="Train examples">{run.train_examples}</ResultRow>
        ) : null}
        {run.validation_examples != null ? (
          <ResultRow label="Val examples">{run.validation_examples}</ResultRow>
        ) : null}
        {run.validation_score != null ? (
          <ResultRow label="Val score">
            <ScoreBadge score={run.validation_score} />
          </ResultRow>
        ) : null}
        {run.phase ? <ResultRow label="Phase">{phaseLabel(run.phase)}</ResultRow> : null}
        <ResultRow label="Started">{formatRelativeTime(run.started_at)}</ResultRow>
        <ResultRow label="Duration">{formatDuration(run.started_at, run.completed_at)}</ResultRow>
        {run.output_path ? (
          <ResultRow label="Output">
            <code className="text-xs">{run.output_path}</code>
          </ResultRow>
        ) : null}
        {run.manifest_path ? (
          <ResultRow label="Manifest">
            <code className="text-xs">{run.manifest_path}</code>
          </ResultRow>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RunListItem({
  run,
  isSelected,
  onSelect,
}: {
  run: OptimizationRunSummary;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors hover:bg-muted/50 ${
        isSelected ? "border-primary/30 bg-muted/30" : "border-border-subtle"
      }`}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">
            #{run.id}
            {run.module_slug ? ` · ${run.module_slug}` : ""}
          </span>
          <StatusBadge status={run.status} />
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{formatRelativeTime(run.started_at)}</span>
          {run.validation_score != null ? (
            <>
              <span>·</span>
              <span>Score: {run.validation_score.toFixed(3)}</span>
            </>
          ) : null}
          {run.status === "running" && run.phase ? (
            <>
              <span>·</span>
              <span className="italic">{phaseLabel(run.phase)}</span>
            </>
          ) : null}
        </div>
      </div>
    </button>
  );
}

export function OptimizationRuns() {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  const runsQuery = useQuery({
    queryKey: optimizationKeys.runsList(
      statusFilter !== "all" ? { status: statusFilter } : undefined,
    ),
    queryFn: ({ signal }) =>
      optimizationEndpoints.listRuns(
        statusFilter !== "all" ? { status: statusFilter } : undefined,
        signal,
      ),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.some((r) => r.status === "running")) return 3_000;
      return 15_000;
    },
  });

  const selectedRun = runsQuery.data?.find((r) => r.id === selectedRunId) ?? null;

  const filterLabel =
    statusFilter === "all"
      ? "All runs"
      : statusFilter === "running"
        ? "Running"
        : statusFilter === "completed"
          ? "Completed"
          : "Failed";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <Select
          value={statusFilter}
          onValueChange={(v) => {
            if (v) setStatusFilter(v);
          }}
        >
          <SelectTrigger className="w-[160px]" aria-label="Filter runs by status">
            <SelectValue>{filterLabel}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All runs</SelectItem>
            <SelectItem value="running">Running</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => runsQuery.refetch()}
          disabled={runsQuery.isFetching}
        >
          {runsQuery.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {runsQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
        </div>
      ) : runsQuery.isError ? (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Failed to load runs:{" "}
              {runsQuery.error instanceof Error ? runsQuery.error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      ) : !runsQuery.data?.length ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <p className="text-sm text-muted-foreground">No optimization runs yet.</p>
          <p className="text-xs text-muted-foreground">
            Start a new run from the &quot;New Run&quot; tab.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {runsQuery.data.map((run) => (
            <RunListItem
              key={run.id}
              run={run}
              isSelected={selectedRunId === run.id}
              onSelect={() => setSelectedRunId(selectedRunId === run.id ? null : run.id)}
            />
          ))}
        </div>
      )}

      {selectedRun ? (
        <>
          <Separator />
          <RunDetailPanel run={selectedRun} />
        </>
      ) : null}
    </div>
  );
}
