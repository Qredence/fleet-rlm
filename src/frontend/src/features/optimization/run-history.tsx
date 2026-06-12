import { FileText, RefreshCw } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import type { OptimizationRunResponse } from "@/lib/rlm-api";

import { errorMessage, formatDateTime, formatScore, targetLabel } from "./optimization-format";

const statusTone: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  running: "secondary",
  failed: "destructive",
};

function StatusBadge({ status }: { status: string }) {
  return <Badge variant={statusTone[status] ?? "outline"}>{status}</Badge>;
}

export function RunHistory({
  runs,
  isLoading,
  error,
  refetch,
  onSelectRun,
}: {
  runs: OptimizationRunResponse[];
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
  onSelectRun?: (runId: string) => void;
}) {
  return (
    <SectionCard variant="subtle">
      <SectionCardHeader className="flex-row items-center justify-between gap-4">
        <div className="min-w-0">
          <SectionCardTitle>Run History</SectionCardTitle>
          <SectionCardDescription>
            {runs.length} GEPA run{runs.length === 1 ? "" : "s"}
          </SectionCardDescription>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={refetch}>
          <RefreshCw data-icon="inline-start" className={cn(isLoading && "animate-spin")} />
          Refresh
        </Button>
      </SectionCardHeader>
      <SectionCardContent>
        {error ? (
          <Alert>
            <AlertTitle>Run history unavailable</AlertTitle>
            <AlertDescription>{errorMessage(error)}</AlertDescription>
          </Alert>
        ) : null}

        {!error && isLoading && runs.length === 0 ? (
          <div className="py-10 text-center text-muted-foreground typo-label">Loading runs...</div>
        ) : null}

        {!error && !isLoading && runs.length === 0 ? (
          <div className="py-10 text-center text-muted-foreground typo-label">
            No optimization runs yet.
          </div>
        ) : null}

        {runs.length > 0 ? (
          <div className="overflow-x-auto">
            <div className="min-w-[1080px] rounded-lg border border-border-subtle">
              <div className="grid grid-cols-[104px_1.3fr_1fr_80px_80px_92px_1.4fr_92px] border-b border-border-subtle bg-muted/35 px-3 py-2 text-xs font-medium text-muted-foreground">
                <span>Status</span>
                <span>Target</span>
                <span>Reflection</span>
                <span>Auto</span>
                <span>Score</span>
                <span>Phase</span>
                <span>Artifacts</span>
                <span>Details</span>
              </div>
              {runs.map((run) => (
                <div
                  key={run.id}
                  className="grid grid-cols-[104px_1.3fr_1fr_80px_80px_92px_1.4fr_92px] border-b border-border-subtle px-3 py-3 text-sm last:border-b-0"
                >
                  <div className="min-w-0">
                    <StatusBadge status={run.status} />
                    <div className="mt-1 truncate text-xs text-muted-foreground">
                      {formatDateTime(run.started_at)}
                    </div>
                  </div>
                  <div className="min-w-0">
                    <div className="truncate font-medium">{targetLabel(run)}</div>
                    <div className="truncate text-xs text-muted-foreground">{run.optimizer}</div>
                    {run.error ? (
                      <div className="mt-1 truncate text-xs text-destructive">{run.error}</div>
                    ) : null}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-xs">{run.reflection_model_id ?? "default"}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {run.reflection_profile_id ?? ""}
                    </div>
                  </div>
                  <span className="text-muted-foreground">{run.auto ?? "-"}</span>
                  <span className="font-mono text-xs">{formatScore(run.validation_score)}</span>
                  <span className="truncate text-muted-foreground">{run.phase ?? "-"}</span>
                  <div className="min-w-0">
                    <div className="truncate text-xs">{run.output_path ?? "-"}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {run.distilled_trace_bundle_path ?? run.manifest_path ?? ""}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onSelectRun?.(run.id)}
                    >
                      <FileText data-icon="inline-start" />
                      Details
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </SectionCardContent>
    </SectionCard>
  );
}
