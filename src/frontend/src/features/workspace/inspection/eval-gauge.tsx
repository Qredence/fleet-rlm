/**
 * EvalGauge — renders the latest MLflow GenAI evaluation run inside the inspection panel.
 *
 * Fetches the list of evaluation runs, selects the most recent, and displays:
 * - 4 LLM-as-judge scores (answer_relevance, faithfulness_to_context, trajectory_coherence, tool_selection_quality)
 * - 6 programmatic metrics (timeout_compliance, trace_completeness, token_cost, latency_p95, routing_correctness, trajectory_redundancy)
 *
 * VAL-C-019: Renders all 10 metrics with labeled rows
 * VAL-C-020: Renders without console errors
 * VAL-C-050: Fetches latest run_id before rendering, shows empty state if no runs exist
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, BarChart3 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { evaluationsEndpoints } from "@/lib/rlm-api/evaluations";
import type { EvaluationReportResponse } from "@/lib/rlm-api/evaluations";
import { inspectorStyles, inspectorInsetClass } from "./inspector-styles";

const JUDGE_METRICS = [
  "answer_relevance",
  "faithfulness_to_context",
  "trajectory_coherence",
  "tool_selection_quality",
] as const;

const PROGRAMMATIC_METRICS = [
  "timeout_compliance",
  "trace_completeness",
  "token_cost",
  "latency_p95",
  "routing_correctness",
  "trajectory_redundancy",
] as const;

function formatMetricLabel(key: string): string {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatMetricValue(key: string, value: number | null | undefined): string {
  if (value == null) return "—";

  // Token cost is a sum, not a ratio
  if (key === "token_cost") {
    return Math.round(value).toLocaleString();
  }

  // Latency is in seconds
  if (key === "latency_p95") {
    return `${value.toFixed(2)}s`;
  }

  // Trajectory redundancy is a count
  if (key === "trajectory_redundancy") {
    return Math.round(value).toString();
  }

  // All other metrics are ratios 0.0-1.0
  return value.toFixed(2);
}

function getMetricTone(value: number | null | undefined): "default" | "strong" | "warning" | "error" {
  if (value == null) return "default";

  // For most metrics, higher is better
  if (value >= 0.7) return "strong";
  if (value >= 0.4) return "warning";
  return "error";
}

function MetricRow({ label, value }: { label: string; value: number | null | undefined }) {
  const tone = getMetricTone(value);
  const formatted = formatMetricValue(label, value);

  return (
    <div className={inspectorInsetClass(tone)}>
      <div className="flex items-center justify-between gap-3">
        <span className="typo-body-xs font-medium text-foreground">{formatMetricLabel(label)}</span>
        <span className="typo-body-xs font-semibold tabular-nums text-foreground">{formatted}</span>
      </div>
    </div>
  );
}

function EmptyEvalState() {
  return (
    <Card className={inspectorStyles.card.root}>
      <CardHeader className={inspectorStyles.card.header}>
        <CardTitle className="flex items-center gap-2">
          <BarChart3 className="size-4" />
          Eval Gauge
        </CardTitle>
      </CardHeader>
      <CardContent className={inspectorStyles.card.content}>
        <div className={inspectorInsetClass("default")}>
          <p className="typo-body-sm text-muted-foreground">
            No evaluation runs available. Run an evaluation to see quality metrics here.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function EvalReportView({ report }: { report: EvaluationReportResponse }) {
  // Compute aggregate means for display
  const aggregateMeans = useMemo(() => {
    const means: Record<string, number | null> = {};

    // Extract means from aggregates
    for (const key of [...JUDGE_METRICS, ...PROGRAMMATIC_METRICS]) {
      const agg = report.aggregates[key];
      means[key] = agg?.mean ?? null;
    }

    return means;
  }, [report]);

  return (
    <Card className={inspectorStyles.card.root}>
      <CardHeader className={inspectorStyles.card.header}>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="size-4" />
            Eval Gauge
          </CardTitle>
          <Badge variant="secondary" className={inspectorStyles.badge.meta}>
            {report.per_trace.length} traces
          </Badge>
        </div>
        <p className="typo-helper text-muted-foreground">
          Run {report.run_id.slice(0, 8)} • {new Date(report.created_at).toLocaleString()}
        </p>
      </CardHeader>

      <CardContent className={inspectorStyles.card.contentStack}>
        {/* Judge Scores Section */}
        <div className={inspectorStyles.stack.section}>
          <h3 className={inspectorStyles.heading.section}>Judge Scores</h3>
          <div className={inspectorStyles.stack.compact}>
            {JUDGE_METRICS.map((key) => (
              <MetricRow key={key} label={key} value={aggregateMeans[key]} />
            ))}
          </div>
        </div>

        {/* Programmatic Metrics Section */}
        <div className={inspectorStyles.stack.section}>
          <h3 className={inspectorStyles.heading.section}>Programmatic Metrics</h3>
          <div className={inspectorStyles.stack.compact}>
            {PROGRAMMATIC_METRICS.map((key) => (
              <MetricRow key={key} label={key} value={aggregateMeans[key]} />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function EvalGauge() {
  // Fetch the list of evaluation runs
  const listQuery = useQuery({
    queryKey: ["workspace", "evaluation-runs"],
    queryFn: () => evaluationsEndpoints.list(),
    staleTime: 30_000, // 30 seconds
  });

  // Get the most recent run (first in the sorted list)
  const latestRun = listQuery.data?.runs?.[0];

  // Fetch the full report for the latest run
  const reportQuery = useQuery({
    queryKey: ["workspace", "evaluation-report", latestRun?.run_id],
    queryFn: () => evaluationsEndpoints.getReport(latestRun!.run_id),
    enabled: Boolean(latestRun),
    staleTime: 60_000, // 1 minute
  });

  // Loading state
  if (listQuery.isLoading || (latestRun && reportQuery.isLoading)) {
    return (
      <Card className={inspectorStyles.card.root}>
        <CardContent className={inspectorStyles.card.content}>
          <div className={inspectorInsetClass("default")}>
            <p className="typo-body-sm text-muted-foreground">Loading evaluation data...</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Error state
  if (listQuery.error) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertTitle>Failed to load evaluations</AlertTitle>
        <AlertDescription>
          {listQuery.error instanceof Error ? listQuery.error.message : "Unknown error"}
        </AlertDescription>
      </Alert>
    );
  }

  // Empty state
  if (!latestRun || !reportQuery.data) {
    return <EmptyEvalState />;
  }

  // Success state
  return <EvalReportView report={reportQuery.data} />;
}
