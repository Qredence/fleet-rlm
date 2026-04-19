import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ChartSparkline } from "@/components/product/chart-sparkline";
import { ScoreBadge } from "@/components/product/score-badge";
import { TimelineStep, type TimelineStepStatus } from "@/components/product/timeline";
import { parseIsoTimestamp } from "@/lib/date";
import { RunDetailDrawer } from "./run-detail-drawer";
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

function formatRelativeTime(isoString: string): string {
  const diffMs = Date.now() - parseIsoTimestamp(isoString).getTime();
  const diffSec = Math.max(0, Math.floor(diffMs / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

const PHASE_ORDER = ["loading", "compiling", "saving"];

function phaseStatus(current: string | null, target: string): TimelineStepStatus {
  if (!current) return "pending";
  const ci = PHASE_ORDER.indexOf(current);
  const ti = PHASE_ORDER.indexOf(target);
  if (ci < 0 || ti < 0) return current === target ? "active" : "pending";
  if (ci > ti) return "complete";
  if (ci === ti) return "active";
  return "pending";
}

function RunCard({
  run,
  selected,
  onToggleSelect,
  onViewResults,
}: {
  run: OptimizationRunSummary;
  selected: boolean;
  onToggleSelect: () => void;
  onViewResults: () => void;
}) {
  const sparkData: number[] = [];
  if (run.validation_score != null) sparkData.push(run.validation_score);

  return (
    <Item
      variant="outline"
      size="sm"
      className={
        selected
          ? "border-primary/30 bg-muted/30 hover:bg-muted/50"
          : "border-border-subtle hover:bg-muted/50"
      }
    >
      <ItemMedia className="self-start pt-0.5">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          className="size-4 rounded border-border accent-primary"
          aria-label={`Select run #${run.id}`}
        />
      </ItemMedia>

      <ItemContent>
        <ItemTitle>
          #{run.id}
          {(run.module_slug ?? run.program_spec) ? (
            <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
              {run.module_slug ?? run.program_spec}
            </Badge>
          ) : null}
          <StatusBadge status={run.status} />
          {run.validation_score != null ? (
            <ScoreBadge score={run.validation_score} format="decimal" />
          ) : null}
        </ItemTitle>

        <ItemDescription className="flex items-center gap-3">
          <span>{formatRelativeTime(run.started_at)}</span>
          {sparkData.length > 0 ? <ChartSparkline data={sparkData} width={60} height={18} /> : null}
        </ItemDescription>

        {run.status === "running" ? (
          <div className="flex flex-col gap-0.5 pt-1">
            {PHASE_ORDER.map((phase) => (
              <TimelineStep
                key={phase}
                label={phaseLabel(phase)}
                status={phaseStatus(run.phase, phase)}
              />
            ))}
          </div>
        ) : null}
      </ItemContent>

      <ItemActions>
        {run.status === "completed" ? (
          <Button variant="outline" size="sm" onClick={onViewResults}>
            Results
          </Button>
        ) : null}
      </ItemActions>
    </Item>
  );
}

export function RunsTab({ onCompare }: { onCompare?: (runIds: string[]) => void }) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [drawerRunId, setDrawerRunId] = useState<string | null>(null);

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

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleCompare = useCallback(() => {
    if (selectedIds.size >= 2) {
      onCompare?.([...selectedIds]);
    }
  }, [selectedIds, onCompare]);

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
        <div className="flex items-center gap-2">
          <Select
            value={statusFilter}
            onValueChange={(v) => {
              if (v) setStatusFilter(v);
            }}
          >
            <SelectTrigger className="w-select-md" aria-label="Filter runs by status">
              <SelectValue>{filterLabel}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All runs</SelectItem>
              <SelectItem value="running">Running</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>

          {selectedIds.size >= 2 ? (
            <Button variant="secondary" size="sm" onClick={handleCompare}>
              Compare ({selectedIds.size})
            </Button>
          ) : null}
        </div>

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
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
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
          <p className="text-xs text-muted-foreground">Start a new run from the Modules tab.</p>
        </div>
      ) : (
        <ItemGroup className="gap-2">
          {runsQuery.data.map((run) => (
            <RunCard
              key={run.id}
              run={run}
              selected={selectedIds.has(run.id)}
              onToggleSelect={() => toggleSelect(run.id)}
              onViewResults={() => setDrawerRunId(run.id)}
            />
          ))}
        </ItemGroup>
      )}

      <RunDetailDrawer
        runId={drawerRunId}
        open={drawerRunId != null}
        onOpenChange={(open) => {
          if (!open) setDrawerRunId(null);
        }}
      />
    </div>
  );
}
