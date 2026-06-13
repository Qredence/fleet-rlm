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
  return (
    <Badge
      variant={statusTone[status] ?? "outline"}
      className="capitalize font-medium px-2 py-0.5 typo-body-xs leading-none"
    >
      {status}
    </Badge>
  );
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
    <SectionCard
      variant="subtle"
      className="border-border bg-card shadow-sm transition-all duration-200"
    >
      <SectionCardHeader className="flex-row items-center justify-between gap-4 border-b border-border-subtle bg-muted/10 px-6 py-4">
        <div className="min-w-0">
          <SectionCardTitle className="text-sm font-semibold tracking-tight">
            Run History
          </SectionCardTitle>
          <SectionCardDescription className="text-muted-foreground typo-helper mt-0.5">
            {runs.length} GEPA run{runs.length === 1 ? "" : "s"}
          </SectionCardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={refetch}
          className="h-8 font-medium transition-colors hover:bg-muted/40 shadow-none border-input"
        >
          <RefreshCw
            data-icon="inline-start"
            className={cn("size-3.5", isLoading && "animate-spin")}
          />
          Refresh
        </Button>
      </SectionCardHeader>
      <SectionCardContent className="p-6">
        {error ? (
          <Alert className="border-border-subtle bg-muted/10 rounded-lg">
            <AlertTitle className="text-sm font-semibold text-foreground">
              Run history unavailable
            </AlertTitle>
            <AlertDescription className="text-xs text-muted-foreground mt-0.5">
              {errorMessage(error)}
            </AlertDescription>
          </Alert>
        ) : null}

        {!error && isLoading && runs.length === 0 ? (
          <div className="py-12 text-center text-muted-foreground typo-body-sm flex flex-col items-center justify-center gap-3">
            <RefreshCw className="size-5 animate-spin text-muted-foreground/60" />
            Loading runs...
          </div>
        ) : null}

        {!error && !isLoading && runs.length === 0 ? (
          <div className="py-12 text-center text-muted-foreground typo-body-sm">
            No optimization runs yet. Create a run to get started.
          </div>
        ) : null}

        {runs.length > 0 ? (
          <div className="overflow-x-auto rounded-lg border border-border-subtle scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent">
            <div className="min-w-[1080px] bg-background">
              {/* Header row */}
              <div className="grid grid-cols-[112px_1.3fr_1fr_80px_80px_92px_1.4fr_92px] items-center border-b border-border-subtle bg-muted/20 px-4 py-2.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                <span>Status</span>
                <span>Target</span>
                <span>Reflection</span>
                <span>Auto</span>
                <span>Score</span>
                <span>Phase</span>
                <span>Artifacts</span>
                <span className="text-right">Action</span>
              </div>
              {/* Data rows */}
              {runs.map((run) => (
                <div
                  key={run.id}
                  onClick={() => onSelectRun?.(run.id)}
                  className="grid grid-cols-[112px_1.3fr_1fr_80px_80px_92px_1.4fr_92px] items-center border-b border-border-subtle px-4 py-3.5 text-sm transition-colors duration-150 hover:bg-muted/15 cursor-pointer last:border-b-0"
                >
                  <div className="min-w-0 pr-2">
                    <StatusBadge status={run.status} />
                    <div className="mt-1.5 truncate typo-body-xs text-muted-foreground leading-none font-mono">
                      {formatDateTime(run.started_at)}
                    </div>
                  </div>
                  <div className="min-w-0 pr-4">
                    <div className="truncate font-semibold text-foreground text-xs">
                      {targetLabel(run)}
                    </div>
                    <div className="truncate typo-helper text-muted-foreground mt-0.5 font-mono uppercase tracking-wider">
                      {run.optimizer}
                    </div>
                    {run.error ? (
                      <div className="mt-1 truncate typo-body-xs text-destructive leading-tight">
                        {run.error}
                      </div>
                    ) : null}
                  </div>
                  <div className="min-w-0 pr-4">
                    <div className="truncate text-xs text-foreground font-medium">
                      {run.reflection_model_id ?? "default"}
                    </div>
                    <div className="truncate typo-helper text-muted-foreground font-mono mt-0.5">
                      {run.reflection_profile_id ?? ""}
                    </div>
                  </div>
                  <span className="text-muted-foreground text-xs font-medium">
                    {run.auto ?? "-"}
                  </span>
                  <span className="font-mono text-xs font-semibold text-foreground">
                    {formatScore(run.validation_score)}
                  </span>
                  <span className="truncate text-muted-foreground text-xs font-medium">
                    {run.phase ?? "-"}
                  </span>
                  <div className="min-w-0 pr-4">
                    <div className="truncate text-xs text-foreground font-mono">
                      {run.output_path ?? "-"}
                    </div>
                    <div className="truncate typo-helper text-muted-foreground font-mono mt-0.5">
                      {run.distilled_trace_bundle_path ?? run.manifest_path ?? ""}
                    </div>
                  </div>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={(e) => {
                        e.stopPropagation(); // prevent row click trigger duplication
                        onSelectRun?.(run.id);
                      }}
                      className="h-8 text-xs font-medium transition-colors hover:bg-muted/40 shadow-none border-input"
                    >
                      <FileText className="size-3.5" data-icon="inline-start" />
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
