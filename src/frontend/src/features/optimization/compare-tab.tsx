import { useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { DataTable, type ColumnDef } from "@/components/product/data-table";
import { ScoreBadge } from "@/components/product/score-badge";
import { DiffViewer } from "@/components/product/diff-viewer";
import {
  comparisonEndpoints,
  evaluationEndpoints,
  optimizationKeys,
  type RunComparisonItem,
  type EvaluationResultItem,
} from "@/lib/rlm-api/optimization";

/* -------------------------------------------------------------------------- */
/*                           Score delta table types                           */
/* -------------------------------------------------------------------------- */

interface ScoreDeltaRow extends Record<string, unknown> {
  runId: number;
  programSpec: string;
  score: number | null;
  delta: number | null;
}

function buildScoreTable(runs: RunComparisonItem[]): ScoreDeltaRow[] {
  if (runs.length === 0) return [];
  const baselineScore = runs[0]?.validation_score ?? null;
  return runs.map((r) => ({
    runId: r.run_id,
    programSpec: r.program_spec,
    score: r.validation_score ?? null,
    delta:
      r.validation_score != null && baselineScore != null
        ? r.validation_score - baselineScore
        : null,
  }));
}

const scoreDeltaColumns: ColumnDef<ScoreDeltaRow>[] = [
  {
    header: "Run",
    accessor: (row) => `#${row.runId}`,
    className: "w-20 tabular-nums",
  },
  {
    header: "Program",
    accessor: (row) => <code className="text-xs">{row.programSpec}</code>,
  },
  {
    header: "Score",
    accessor: (row) =>
      row.score != null ? (
        <ScoreBadge score={row.score} format="decimal" />
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    className: "w-24",
  },
  {
    header: "Delta",
    accessor: (row) => {
      if (row.delta == null) return <span className="text-muted-foreground">—</span>;
      if (row.delta === 0) return <span className="tabular-nums text-muted-foreground">0.00</span>;
      const sign = row.delta > 0 ? "+" : "";
      const color = row.delta > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive";
      return (
        <span className={`tabular-nums font-medium ${color}`}>
          {sign}
          {row.delta.toFixed(2)}
        </span>
      );
    },
    className: "w-24",
  },
];

/* -------------------------------------------------------------------------- */
/*                      Per-example score breakdown types                      */
/* -------------------------------------------------------------------------- */

interface ExampleScoreRow extends Record<string, unknown> {
  exampleIndex: number;
  scoreA: number | null;
  scoreB: number | null;
  delta: number | null;
}

function buildExampleScoreTable(
  resultsA: EvaluationResultItem[],
  resultsB: EvaluationResultItem[],
): ExampleScoreRow[] {
  const mapB = new Map<number, number>();
  for (const r of resultsB) {
    mapB.set(r.example_index, r.score);
  }

  const allIndices = new Set<number>();
  for (const r of resultsA) allIndices.add(r.example_index);
  for (const r of resultsB) allIndices.add(r.example_index);

  return [...allIndices]
    .sort((a, b) => a - b)
    .map((idx) => {
      const aItem = resultsA.find((r) => r.example_index === idx);
      const bItem = resultsB.find((r) => r.example_index === idx);
      const scoreA = aItem?.score ?? null;
      const scoreB = bItem?.score ?? null;
      return {
        exampleIndex: idx,
        scoreA,
        scoreB,
        delta: scoreA != null && scoreB != null ? scoreB - scoreA : null,
      };
    });
}

const exampleScoreColumns: ColumnDef<ExampleScoreRow>[] = [
  {
    header: "Example #",
    accessor: (row) => row.exampleIndex,
    className: "w-24 tabular-nums",
  },
  {
    header: "Run A Score",
    accessor: (row) =>
      row.scoreA != null ? (
        <ScoreBadge score={row.scoreA} format="decimal" />
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    className: "w-28",
  },
  {
    header: "Run B Score",
    accessor: (row) =>
      row.scoreB != null ? (
        <ScoreBadge score={row.scoreB} format="decimal" />
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    className: "w-28",
  },
  {
    header: "Delta",
    accessor: (row) => {
      if (row.delta == null) return <span className="text-muted-foreground">—</span>;
      if (row.delta === 0) return <span className="tabular-nums text-muted-foreground">0.00</span>;
      const sign = row.delta > 0 ? "+" : "";
      const color = row.delta > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive";
      return (
        <span className={`tabular-nums font-medium ${color}`}>
          {sign}
          {row.delta.toFixed(2)}
        </span>
      );
    },
    className: "w-24",
  },
];

/* -------------------------------------------------------------------------- */
/*                          Score delta banner helper                          */
/* -------------------------------------------------------------------------- */

function ScoreDeltaSummary({ runs }: { runs: RunComparisonItem[] }) {
  if (runs.length < 2) return null;
  const first = runs[0]!;
  const last = runs[runs.length - 1]!;
  const scoreA = first.validation_score ?? null;
  const scoreB = last.validation_score ?? null;
  if (scoreA == null || scoreB == null) return null;

  const delta = scoreB - scoreA;
  const sign = delta > 0 ? "+" : "";
  const deltaColor =
    delta > 0
      ? "text-emerald-600 dark:text-emerald-400"
      : delta < 0
        ? "text-destructive"
        : "text-muted-foreground";

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-3 py-4">
        <span className="text-sm text-muted-foreground">Run #{first.run_id}:</span>
        <ScoreBadge score={scoreA} format="decimal" />
        <span className="text-muted-foreground" aria-hidden>
          →
        </span>
        <span className="text-sm text-muted-foreground">Run #{last.run_id}:</span>
        <ScoreBadge score={scoreB} format="decimal" />
        <span className={`ml-1 text-lg font-semibold tabular-nums ${deltaColor}`}>
          ({sign}
          {delta.toFixed(2)})
        </span>
      </CardContent>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*                        Prompt diffs grouped by predictor                    */
/* -------------------------------------------------------------------------- */

interface PromptDiff {
  predictor: string;
  before: string;
  after: string;
}

function buildPromptDiffs(runs: RunComparisonItem[]): PromptDiff[] {
  if (runs.length < 2) return [];
  const firstRun = runs[0];
  const lastRun = runs[runs.length - 1];
  if (!firstRun || !lastRun) return [];

  const beforeMap = new Map<string, string>();
  for (const snap of firstRun.prompt_snapshots) {
    if (snap.prompt_type === "before" || firstRun.prompt_snapshots.length <= 2) {
      beforeMap.set(snap.predictor_name, snap.prompt_text);
    }
  }

  const diffs: PromptDiff[] = [];
  for (const snap of lastRun.prompt_snapshots) {
    const beforeText = beforeMap.get(snap.predictor_name);
    if (beforeText != null && snap.prompt_text !== beforeText) {
      diffs.push({
        predictor: snap.predictor_name,
        before: beforeText,
        after: snap.prompt_text,
      });
    }
  }
  return diffs;
}

/** Group diffs by predictor name, preserving insertion order. */
function groupByPredictor(diffs: PromptDiff[]): Map<string, PromptDiff[]> {
  const groups = new Map<string, PromptDiff[]>();
  for (const diff of diffs) {
    const existing = groups.get(diff.predictor);
    if (existing) {
      existing.push(diff);
    } else {
      groups.set(diff.predictor, [diff]);
    }
  }
  return groups;
}

/* -------------------------------------------------------------------------- */
/*                               Main component                               */
/* -------------------------------------------------------------------------- */

export function CompareTab({ initialRunIds }: { initialRunIds?: number[] }) {
  const [runIdsInput, setRunIdsInput] = useState(initialRunIds?.join(", ") ?? "");
  const [activeRunIds, setActiveRunIds] = useState<number[]>(initialRunIds ?? []);

  const comparisonQuery = useQuery({
    queryKey: optimizationKeys.runComparison(activeRunIds),
    queryFn: ({ signal }) => comparisonEndpoints.compare(activeRunIds, signal),
    enabled: activeRunIds.length >= 2,
    staleTime: 30_000,
  });

  // Fetch per-example evaluation results for first and last run
  const firstRunId = activeRunIds.length >= 2 ? activeRunIds[0] : undefined;
  const lastRunId = activeRunIds.length >= 2 ? activeRunIds[activeRunIds.length - 1] : undefined;

  const evalQueries = useQueries({
    queries: [firstRunId, lastRunId]
      .filter((id): id is number => id != null)
      .map((runId) => ({
        queryKey: optimizationKeys.runResults(runId, { limit: 200 }),
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          evaluationEndpoints.getResults(runId, { limit: 200 }, signal),
        enabled: comparisonQuery.isSuccess,
        staleTime: 60_000,
      })),
  });

  const handleCompare = () => {
    const parsed = runIdsInput
      .split(/[,\s]+/)
      .map((s) => Number.parseInt(s.trim(), 10))
      .filter((n) => !Number.isNaN(n) && n > 0);
    if (parsed.length >= 2) {
      setActiveRunIds(parsed);
    }
  };

  const runs = useMemo(() => comparisonQuery.data?.runs ?? [], [comparisonQuery.data]);
  const scoreTable = buildScoreTable(runs);
  const promptDiffs = useMemo(() => buildPromptDiffs(runs), [runs]);
  const groupedDiffs = useMemo(() => groupByPredictor(promptDiffs), [promptDiffs]);

  // Build per-example breakdown when both eval queries succeed
  const exampleScoreTable = useMemo(() => {
    const resultsA = evalQueries[0]?.data?.items;
    const resultsB = evalQueries[1]?.data?.items;
    if (!resultsA?.length || !resultsB?.length) return [];
    return buildExampleScoreTable(resultsA, resultsB);
  }, [evalQueries]);

  return (
    <div className="flex flex-col gap-6">
      {/* Run ID input */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Select Runs to Compare</CardTitle>
        </CardHeader>
        <CardContent className="flex items-end gap-3">
          <div className="flex flex-1 flex-col gap-1.5">
            <label htmlFor="compare-run-ids" className="text-xs text-muted-foreground">
              Enter two or more run IDs (comma-separated)
            </label>
            <Input
              id="compare-run-ids"
              value={runIdsInput}
              onChange={(e) => setRunIdsInput(e.target.value)}
              placeholder="1, 2, 3"
              autoComplete="off"
            />
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleCompare}
            disabled={comparisonQuery.isFetching}
          >
            {comparisonQuery.isFetching ? "Loading…" : "Compare"}
          </Button>
        </CardContent>
      </Card>

      {/* Results */}
      {activeRunIds.length < 2 ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <p className="text-sm text-muted-foreground">
            Enter at least two run IDs above, or use the Compare button on the Runs tab.
          </p>
        </div>
      ) : comparisonQuery.isLoading ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32 w-full rounded-lg" />
          <Skeleton className="h-48 w-full rounded-lg" />
        </div>
      ) : comparisonQuery.isError ? (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Failed to load comparison:{" "}
              {comparisonQuery.error instanceof Error
                ? comparisonQuery.error.message
                : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* A — Overall score delta banner */}
          <ScoreDeltaSummary runs={runs} />

          {/* Score delta table */}
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Score Comparison</h3>
            <DataTable
              columns={scoreDeltaColumns}
              data={scoreTable}
              emptyMessage="No score data available."
              rowKey={(row) => row.runId}
            />
          </div>

          {/* B — Prompt diffs grouped by predictor */}
          {promptDiffs.length > 0 ? (
            <div className="flex flex-col gap-4">
              <h3 className="text-sm font-medium">Prompt Diffs</h3>
              {[...groupedDiffs.entries()].map(([predictor, diffs]) => (
                <div key={predictor} className="flex flex-col gap-3">
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    Predictor: {predictor}
                  </h4>
                  {diffs.map((diff, i) => (
                    <DiffViewer
                      key={`${predictor}-${i}`}
                      title={diffs.length > 1 ? `${predictor} (${i + 1})` : predictor}
                      before={diff.before}
                      after={diff.after}
                      mode="unified"
                    />
                  ))}
                </div>
              ))}
            </div>
          ) : runs.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              No prompt differences detected between compared runs.
            </p>
          ) : null}

          {/* C — Per-example score breakdown */}
          {exampleScoreTable.length > 0 && (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Per-Example Score Breakdown</h3>
              <DataTable
                columns={exampleScoreColumns}
                data={exampleScoreTable}
                pageSize={20}
                emptyMessage="No per-example results available."
                rowKey={(row) => row.exampleIndex}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
