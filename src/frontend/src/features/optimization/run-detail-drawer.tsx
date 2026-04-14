import { useQuery } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { DetailDrawer } from "@/components/product/detail-drawer";
import { DataTable, type ColumnDef } from "@/components/product/data-table";
import { KeyValueGrid } from "@/components/product";
import { ScoreBadge } from "@/components/product/score-badge";
import {
  evaluationEndpoints,
  optimizationEndpoints,
  optimizationKeys,
  type EvaluationResultItem,
  type OptimizationRunSummary,
} from "@/lib/rlm-api/optimization";

const resultColumns: ColumnDef<EvaluationResultItem>[] = [
  {
    header: "#",
    accessor: (row) => row.example_index,
    className: "w-12 text-right tabular-nums",
  },
  {
    header: "Input",
    accessor: (row) => (
      <span className="line-clamp-2 text-xs" title={row.input_data}>
        {row.input_data}
      </span>
    ),
    className: "max-w-[200px]",
  },
  {
    header: "Expected",
    accessor: (row) => (
      <span className="line-clamp-2 text-xs" title={row.expected_output ?? undefined}>
        {row.expected_output ?? "—"}
      </span>
    ),
    className: "max-w-[180px]",
  },
  {
    header: "Predicted",
    accessor: (row) => (
      <span className="line-clamp-2 text-xs" title={row.predicted_output ?? undefined}>
        {row.predicted_output ?? "—"}
      </span>
    ),
    className: "max-w-[180px]",
  },
  {
    header: "Score",
    accessor: (row) => <ScoreBadge score={row.score} format="decimal" />,
    sortable: true,
    className: "w-20",
  },
];

export function RunDetailDrawer({
  runId,
  open,
  onOpenChange,
}: {
  runId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const runQuery = useQuery({
    queryKey: optimizationKeys.runDetail(runId ?? 0),
    queryFn: ({ signal }) => optimizationEndpoints.getRun(runId!, signal),
    enabled: open && runId != null,
    staleTime: 30_000,
  });

  const resultsQuery = useQuery({
    queryKey: optimizationKeys.runResults(runId ?? 0),
    queryFn: ({ signal }) => evaluationEndpoints.getResults(runId!, undefined, signal),
    enabled: open && runId != null,
    staleTime: 30_000,
  });

  return (
    <DetailDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={runId != null ? `Run #${runId} — Results` : "Run Results"}
      description="Per-example evaluation scores."
      className="sm:max-w-xl md:max-w-2xl"
    >
      {/* ── Configuration ─────────────────────────────────────── */}
      {runQuery.isLoading ? (
        <div className="flex flex-col gap-2 py-4">
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-6 w-1/2" />
        </div>
      ) : runQuery.data ? (
        <section className="space-y-2 pb-4">
          <h3 className="text-sm font-semibold">Configuration</h3>
          <KeyValueGrid items={buildConfigItems(runQuery.data)} columns={2} />
        </section>
      ) : null}

      {/* ── Results table ─────────────────────────────────────── */}
      {resultsQuery.isLoading ? (
        <div className="flex flex-col gap-2 py-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : resultsQuery.isError ? (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Failed to load results:{" "}
              {resultsQuery.error instanceof Error ? resultsQuery.error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      ) : (
        <DataTable
          columns={resultColumns}
          data={resultsQuery.data?.items ?? []}
          pageSize={15}
          emptyMessage="No evaluation results for this run."
          rowKey={(row) => row.id}
        />
      )}
    </DetailDrawer>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────

function formatDuration(startedAt: string, completedAt: string | null | undefined): string {
  if (!completedAt) return "—";
  const ms = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 1_000) return `${ms}ms`;
  const secs = ms / 1_000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = Math.round(secs % 60);
  return `${mins}m ${remSecs}s`;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString();
}

function buildConfigItems(run: OptimizationRunSummary) {
  const items: Array<{ label: string; value: React.ReactNode }> = [
    { label: "Module", value: run.module_slug },
    { label: "Dataset", value: run.dataset_path?.split("/").pop() ?? run.dataset_path },
    { label: "Status", value: run.phase ? `${run.status} · ${run.phase}` : run.status },
    { label: "Auto level", value: run.auto },
    { label: "Train ratio", value: run.train_ratio != null ? String(run.train_ratio) : null },
    {
      label: "Split",
      value:
        run.train_examples != null || run.validation_examples != null
          ? `${run.train_examples ?? "?"} train / ${run.validation_examples ?? "?"} val`
          : null,
    },
    { label: "Created", value: formatTimestamp(run.started_at) },
    { label: "Duration", value: formatDuration(run.started_at, run.completed_at) },
  ];
  return items;
}
