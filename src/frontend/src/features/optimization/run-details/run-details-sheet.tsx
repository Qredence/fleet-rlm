import { FileText, FlaskConical } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CodeBlock, CodeBlockCode } from "@/components/ui/code-block";
import type {
  OptimizationPromotionDraftResponse,
  OptimizationRunDetailResponse,
} from "@/lib/rlm-api";

import { errorMessage, formatScore, shortPath, targetLabel } from "../optimization-format";

function holdoutIsPromotionReady(
  detail: OptimizationRunDetailResponse | undefined,
): boolean | null {
  const value = detail?.typed_review_bundle?.holdout?.promotion_ready;
  return typeof value === "boolean" ? value : null;
}

function gepaEvidencePath(detail: OptimizationRunDetailResponse | undefined): string | null {
  const path = detail?.artifact_refs.find((ref) => ref.kind === "gepa_evidence")?.path;
  return path ?? null;
}

function RunDetailMetric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-border-subtle bg-muted/20 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate font-mono text-sm">{value}</div>
    </div>
  );
}

function PathRow({ label, path, exists }: { label: string; path: string; exists?: boolean }) {
  return (
    <div className="min-w-0 rounded-md border border-border-subtle px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {typeof exists === "boolean" ? (
          <Badge variant={exists ? "outline" : "secondary"}>{exists ? "exists" : "missing"}</Badge>
        ) : null}
      </div>
      <div className="mt-1 break-all font-mono text-xs">{path}</div>
    </div>
  );
}

export function RunDetailsSheet({
  runId,
  open,
  onOpenChange,
  detail,
  isLoading,
  error,
  draft,
  isDraftPending,
  onCreateDraft,
}: {
  runId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  detail: OptimizationRunDetailResponse | undefined;
  isLoading: boolean;
  error: unknown;
  draft: OptimizationPromotionDraftResponse | null;
  isDraftPending: boolean;
  onCreateDraft: () => void;
}) {
  const run = detail?.run;
  const manifestText = detail?.manifest ? JSON.stringify(detail.manifest, null, 2) : "";
  const promptDiffs = detail?.prompt_diffs ?? [];
  const traceEvidence = detail?.trace_evidence ?? [];
  const candidateDecisions = detail?.candidate_decisions ?? [];
  const artifactRefs = detail?.artifact_refs ?? [];
  const promotionReady = holdoutIsPromotionReady(detail);
  const candidateEvidencePath = gepaEvidencePath(detail);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-sheet-optimization gap-0 p-0 sm:max-w-none">
        <SheetHeader className="border-b border-border-subtle">
          <SheetTitle>GEPA Run Details</SheetTitle>
          <SheetDescription>
            {runId ? `Run ${runId}` : "Select a run"} · {run ? targetLabel(run) : "loading"}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="p-4">
            {error ? (
              <Alert>
                <AlertTitle>Run details unavailable</AlertTitle>
                <AlertDescription>{errorMessage(error)}</AlertDescription>
              </Alert>
            ) : null}

            {!error && isLoading ? (
              <div className="grid gap-3">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-64 w-full" />
              </div>
            ) : null}

            {!error && !isLoading && detail ? (
              <Tabs defaultValue="summary" className="space-y-4">
                <TabsList className="flex flex-wrap">
                  <TabsTrigger value="summary">Summary</TabsTrigger>
                  <TabsTrigger value="prompt">Prompt Diff</TabsTrigger>
                  <TabsTrigger value="trace">Trace Evidence</TabsTrigger>
                  <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
                  <TabsTrigger value="promotion">Promotion Draft</TabsTrigger>
                </TabsList>

                <TabsContent value="summary" className="space-y-4">
                  <Alert>
                    <FileText className="text-muted-foreground" />
                    <AlertTitle>{detail.insights.summary}</AlertTitle>
                    <AlertDescription>{detail.insights.next_step}</AlertDescription>
                  </Alert>
                  <Alert>
                    <FlaskConical className="text-muted-foreground" />
                    <AlertTitle>Self-improving RLM objective</AlertTitle>
                    <AlertDescription>
                      GEPA pairs a proposer RLM with distilled MLflow trace evidence to improve the
                      executor RLM prompt artifact. The result is auditable and non-mutating until a
                      promotion draft is reviewed.
                    </AlertDescription>
                  </Alert>
                  {promotionReady === false ? (
                    <Alert>
                      <AlertTitle>Holdout validation required</AlertTitle>
                      <AlertDescription>
                        GEPA used the trainset as its internal Pareto valset for this run. Review
                        the draft, but add holdout validation examples before promotion.
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  <div className="grid gap-3 md:grid-cols-4">
                    <RunDetailMetric label="Outcome" value={detail.insights.selected_outcome} />
                    <RunDetailMetric
                      label="Baseline"
                      value={formatScore(detail.score_summary.baseline_score)}
                    />
                    <RunDetailMetric
                      label="Optimized"
                      value={formatScore(detail.score_summary.optimized_score)}
                    />
                    <RunDetailMetric
                      label="Delta"
                      value={formatScore(detail.score_summary.score_delta)}
                    />
                  </div>
                  <div className="grid gap-3 md:grid-cols-3">
                    <RunDetailMetric
                      label="Train examples"
                      value={detail.score_summary.train_examples ?? "-"}
                    />
                    <RunDetailMetric
                      label="Validation examples"
                      value={detail.score_summary.validation_examples ?? "-"}
                    />
                    <RunDetailMetric
                      label="Reflection"
                      value={detail.run.reflection_model_id ?? "default"}
                    />
                  </div>
                  <div className="space-y-2">
                    <div className="text-sm font-medium">Candidate decisions</div>
                    {candidateDecisions.map((candidate) => (
                      <div
                        key={candidate.candidate_id}
                        className="rounded-md border border-border-subtle p-3"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            variant={candidate.status === "selected" ? "default" : "secondary"}
                          >
                            {candidate.status}
                          </Badge>
                          <span className="font-medium">{candidate.summary}</span>
                        </div>
                        {candidate.rationale ? (
                          <p className="mt-2 text-sm text-muted-foreground">
                            {candidate.rationale}
                          </p>
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                          {typeof candidate.score === "number" ? (
                            <span>score {formatScore(candidate.score)}</span>
                          ) : null}
                          {typeof candidate.score_delta === "number" ? (
                            <span>delta {formatScore(candidate.score_delta)}</span>
                          ) : null}
                          {candidate.artifact_path ? (
                            <span>{shortPath(candidate.artifact_path)}</span>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </TabsContent>

                <TabsContent value="prompt" className="space-y-4">
                  {promptDiffs.length === 0 ? (
                    <Alert>
                      <AlertTitle>No prompt snapshots</AlertTitle>
                      <AlertDescription>
                        Prompt snapshots were not persisted for this run.
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  {promptDiffs.map((diff) => (
                    <div key={diff.predictor_name} className="space-y-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={diff.changed ? "default" : "secondary"}>
                          {diff.changed ? "changed" : "unchanged"}
                        </Badge>
                        <span className="font-medium">{diff.predictor_name}</span>
                      </div>
                      {!diff.changed ? (
                        <Alert>
                          <AlertTitle>No semantic prompt change selected</AlertTitle>
                          <AlertDescription>
                            GEPA kept the original prompt as the selected artifact for this
                            component.
                          </AlertDescription>
                        </Alert>
                      ) : null}
                      <div className="grid gap-3 xl:grid-cols-2">
                        <CodeBlock>
                          <div className="border-b border-border-subtle px-3 py-2 text-xs font-medium">
                            Before
                          </div>
                          <CodeBlockCode code={diff.before_prompt || ""} language="markdown" />
                        </CodeBlock>
                        <CodeBlock>
                          <div className="border-b border-border-subtle px-3 py-2 text-xs font-medium">
                            After
                          </div>
                          <CodeBlockCode code={diff.after_prompt || ""} language="markdown" />
                        </CodeBlock>
                      </div>
                    </div>
                  ))}
                  {detail.optimized_artifact_text ? (
                    <CodeBlock>
                      <div className="border-b border-border-subtle px-3 py-2 text-xs font-medium">
                        Selected artifact {detail.optimized_artifact_truncated ? "(truncated)" : ""}
                      </div>
                      <CodeBlockCode code={detail.optimized_artifact_text} language="markdown" />
                    </CodeBlock>
                  ) : null}
                </TabsContent>

                <TabsContent value="trace" className="space-y-4">
                  {traceEvidence.length === 0 ? (
                    <Alert>
                      <AlertTitle>No distilled trace evidence</AlertTitle>
                      <AlertDescription>
                        This details response does not render raw spans. Add a distilled trace
                        bundle to show evidence.
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  {traceEvidence.map((item, index) => (
                    <div
                      key={`${item.kind}-${item.trace_id ?? index}`}
                      className="rounded-md border p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline">{item.kind}</Badge>
                        {item.trace_id ? (
                          <span className="font-mono text-xs">{item.trace_id}</span>
                        ) : null}
                        {item.span_count ? (
                          <span className="text-xs text-muted-foreground">
                            {item.span_count} spans
                          </span>
                        ) : null}
                      </div>
                      {(item.failure_categories ?? []).length ? (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {(item.failure_categories ?? []).map((category) => (
                            <Badge key={category} variant="secondary">
                              {category}
                            </Badge>
                          ))}
                        </div>
                      ) : null}
                      {(item.prompt_change_recommendations ?? []).length ? (
                        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                          {(item.prompt_change_recommendations ?? []).map((recommendation) => (
                            <li key={recommendation}>{recommendation}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ))}
                </TabsContent>

                <TabsContent value="artifacts" className="space-y-4">
                  <div className="grid gap-3">
                    {artifactRefs.map((artifact) => (
                      <PathRow
                        key={`${artifact.kind}-${artifact.path}`}
                        label={artifact.label}
                        path={artifact.path}
                        exists={artifact.exists}
                      />
                    ))}
                  </div>
                  {manifestText ? (
                    <CodeBlock>
                      <div className="border-b border-border-subtle px-3 py-2 text-xs font-medium">
                        Manifest JSON
                      </div>
                      <CodeBlockCode code={manifestText} language="json" />
                    </CodeBlock>
                  ) : (
                    <Alert>
                      <AlertTitle>Manifest unavailable</AlertTitle>
                      <AlertDescription>
                        {detail.run.manifest_path
                          ? `Could not read ${shortPath(detail.run.manifest_path)}`
                          : "This run has no manifest path yet."}
                      </AlertDescription>
                    </Alert>
                  )}
                  {candidateEvidencePath ? (
                    <Alert>
                      <AlertTitle>Candidate evidence persisted</AlertTitle>
                      <AlertDescription>{candidateEvidencePath}</AlertDescription>
                    </Alert>
                  ) : null}
                </TabsContent>

                <TabsContent value="promotion" className="space-y-4">
                  <Alert>
                    <AlertTitle>Draft only</AlertTitle>
                    <AlertDescription>
                      Creating a promotion draft records the selected artifact for review. It does
                      not overwrite scaffold skills or live runtime prompts.
                    </AlertDescription>
                  </Alert>
                  <Button type="button" onClick={onCreateDraft} disabled={isDraftPending || !runId}>
                    {isDraftPending ? "Creating draft..." : "Create promotion draft"}
                  </Button>
                  {draft ? (
                    <div className="space-y-3 rounded-md border border-border-subtle p-3">
                      <div className="flex items-center gap-2">
                        <Badge>{draft.status}</Badge>
                        <span className="font-medium">{draft.draft_id}</span>
                      </div>
                      <p className="text-sm text-muted-foreground">{draft.summary}</p>
                      <PathRow label="Draft path" path={draft.draft_path} exists />
                      {draft.optimized_artifact_path ? (
                        <PathRow label="Optimized artifact" path={draft.optimized_artifact_path} />
                      ) : null}
                    </div>
                  ) : null}
                </TabsContent>
              </Tabs>
            ) : null}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
