import { FileQuestion, SearchSlash, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CodeBlock, CodeBlockCode } from "@/components/ui/code-block";
import { Reasoning, ReasoningTrigger, ReasoningContent } from "@/components/ai-elements/reasoning";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { useRunWorkbenchStore } from "@/screens/workspace/use-workspace";
import type {
  ArtifactSummary,
  ContextSourceSummary,
  IterationSummary,
} from "@/screens/workspace/use-workspace";

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function preferredArtifactText(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }

  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  const record = value as Record<string, unknown>;
  for (const key of ["final_markdown", "summary", "text", "content", "message"]) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate;
    }
  }

  const nestedValue = record.value;
  if (nestedValue !== value) {
    return preferredArtifactText(nestedValue);
  }

  return null;
}

function EmptyPanel({
  title,
  description,
  icon: Icon = FileQuestion,
}: {
  title: string;
  description: string;
  icon?: typeof FileQuestion;
}) {
  return (
    <Empty>
      <EmptyMedia variant="icon">
        <Icon />
      </EmptyMedia>
      <EmptyContent>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyContent>
    </Empty>
  );
}

function ArtifactPanel({ artifact }: { artifact?: ArtifactSummary | null }) {
  if (!artifact) {
    return (
      <EmptyPanel
        title="No final output yet"
        description="The final structured output for this run will appear here when execution completes."
        icon={SearchSlash}
      />
    );
  }

  const renderedArtifactText = preferredArtifactText(artifact.value);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {artifact.kind ? <Badge variant="secondary">{artifact.kind}</Badge> : null}
        {artifact.finalizationMode ? (
          <Badge variant="secondary">{artifact.finalizationMode}</Badge>
        ) : null}
        {artifact.variableName ? (
          <Badge variant="secondary">var {artifact.variableName}</Badge>
        ) : null}
      </div>
      {artifact.textPreview ? (
        <Card className="border-border-subtle/80 bg-muted/15">
          <CardContent className="pt-4 text-sm text-foreground">{artifact.textPreview}</CardContent>
        </Card>
      ) : null}
      <CodeBlock className="border-border-subtle/80 bg-muted/15">
        <CodeBlockCode
          code={renderedArtifactText ?? stringifyValue(artifact.value)}
          language="json"
        />
      </CodeBlock>
    </div>
  );
}

function statusBadgeVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "completed") return "default";
  if (status === "error") return "destructive";
  if (status === "running") return "secondary";
  return "outline";
}

function IterationRow({
  iteration,
  selected,
  onSelect,
}: {
  iteration: IterationSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-xl border px-3 py-3 text-left transition-colors",
        selected
          ? "border-accent bg-accent/10"
          : "border-border-subtle/80 bg-muted/15 hover:bg-muted/30",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">iter {iteration.iteration}</Badge>
        <Badge variant={statusBadgeVariant(iteration.status)}>{iteration.status}</Badge>
        {iteration.phase ? <Badge variant="secondary">{iteration.phase}</Badge> : null}
      </div>
      <p className="mt-2 text-sm text-foreground">{iteration.summary}</p>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {iteration.durationMs != null ? <span>{iteration.durationMs}ms</span> : null}
        {iteration.callbackCount != null ? <span>{iteration.callbackCount} callbacks</span> : null}
        {iteration.finalized ? <span>finalized</span> : null}
      </div>
    </button>
  );
}

function IterationDetail({ iteration }: { iteration?: IterationSummary | null }) {
  if (!iteration) {
    return (
      <EmptyPanel
        title="No iteration selected"
        description="Select an iteration to inspect its prompt-response summary and execution output."
        icon={SearchSlash}
      />
    );
  }

  return (
    <Card className="border-border-subtle/80 bg-card/80">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm">Iteration {iteration.iteration}</CardTitle>
          <Badge variant={statusBadgeVariant(iteration.status)}>{iteration.status}</Badge>
          {iteration.phase ? <Badge variant="secondary">{iteration.phase}</Badge> : null}
        </div>
        <CardDescription>{iteration.summary}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {iteration.reasoningSummary ? (
          <Reasoning className="mb-0" defaultOpen>
            <ReasoningTrigger
              getThinkingMessage={() => (
                <span className="text-sm font-medium">Planner reasoning</span>
              )}
            />
            <ReasoningContent className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap">
              {iteration.reasoningSummary}
            </ReasoningContent>
          </Reasoning>
        ) : null}
        {iteration.code ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Executed code
            </div>
            <CodeBlock className="border-border-subtle/80 bg-muted/15">
              <CodeBlockCode code={iteration.code} language="python" />
            </CodeBlock>
          </section>
        ) : null}
        {iteration.stdout ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              STDOUT
            </div>
            <CodeBlock className="border-border-subtle/80 bg-muted/15">
              <CodeBlockCode code={iteration.stdout} language="text" />
            </CodeBlock>
          </section>
        ) : null}
        {iteration.stderr ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              STDERR
            </div>
            <CodeBlock className="border-border-subtle/80 bg-destructive/5 border-destructive/20">
              <CodeBlockCode code={iteration.stderr} language="text" />
            </CodeBlock>
          </section>
        ) : null}
        {iteration.error ? (
          <Alert variant="destructive">
            <AlertTitle>Iteration error</AlertTitle>
            <AlertDescription>{iteration.error}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ContextSourceCard({ source }: { source: ContextSourceSummary }) {
  return (
    <Card className="border-border-subtle/80 bg-muted/15">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm">{source.hostPath}</CardTitle>
          <Badge variant="secondary">{source.kind}</Badge>
          {source.sourceType ? <Badge variant="secondary">{source.sourceType}</Badge> : null}
        </div>
        <CardDescription>
          {source.stagedPath ? `Staged at ${source.stagedPath}` : "Pending staging"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm text-muted-foreground">
        <div className="flex flex-wrap gap-3">
          {source.fileCount != null ? <span>{source.fileCount} files</span> : null}
          {source.skippedCount != null ? <span>{source.skippedCount} skipped</span> : null}
          {source.extractionMethod ? <span>{source.extractionMethod}</span> : null}
        </div>
        {source.warnings?.length ? (
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {source.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function RunWorkbench() {
  const {
    status,
    task,
    repoUrl,
    repoRef,
    contextSources,
    iterations,
    callbacks,
    promptHandles,
    sources,
    attachments,
    activity,
    selectedIterationId,
    selectedTab,
    selectTab,
    selectIteration,
    finalArtifact,
    summary,
    errorMessage,
  } = useRunWorkbenchStore();

  const selectedIteration =
    iterations.find((item) => item.id === selectedIterationId) ?? iterations.at(-1) ?? null;
  const renderedSelectedTab =
    selectedTab === "callbacks" || selectedTab === "prompts" ? "iterations" : selectedTab;
  const warningCount = summary?.warnings?.length ?? 0;

  return (
    <div
      className="flex h-full min-h-0 flex-1 flex-col gap-3 overflow-hidden"
      data-testid="run-workbench"
    >
      {errorMessage ? (
        <Alert variant="destructive" className="shrink-0 border-destructive/30 bg-destructive/5">
          <AlertTitle>Run error</AlertTitle>
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
      ) : null}

      {warningCount > 0 ? (
        <Alert className="shrink-0 border-accent/25 bg-accent/5 text-foreground">
          <TriangleAlert className="size-4" />
          <AlertTitle>Analysis warnings</AlertTitle>
          <AlertDescription>
            <ul className="mt-2 list-disc pl-5">
              {(summary?.warnings ?? []).map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="shrink-0 rounded-xl border border-border-subtle/80 bg-card/80 px-3 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-foreground">
              {task ?? "Workspace execution"}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {repoUrl ? repoUrl : "No repository configured"}
              {repoRef ? ` @ ${repoRef}` : ""}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Workspace execution</Badge>
            <Badge variant={statusBadgeVariant(status)}>{status}</Badge>
            {summary?.terminationReason ? (
              <Badge variant="secondary">{summary.terminationReason}</Badge>
            ) : null}
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{iterations.length} iterations</span>
          <span>{callbacks.length} callbacks</span>
          <span>{promptHandles.length} prompt objects</span>
          <span>{sources.length + attachments.length} evidence items</span>
          {summary?.durationMs != null ? <span>{summary.durationMs}ms</span> : null}
        </div>
      </div>

      <Card className="flex min-h-0 flex-1 flex-col" data-testid="detail-tabs">
        <CardContent className="min-h-0 flex-1 p-0">
          <Tabs
            className="flex h-full min-h-0 flex-col"
            value={renderedSelectedTab}
            onValueChange={(value) => selectTab(value as "iterations" | "evidence" | "final")}
          >
            <div className="shrink-0 overflow-x-auto border-b border-border-subtle/70 px-3 no-scrollbar">
              <TabsList variant="underline">
                <TabsTrigger value="iterations">Iterations</TabsTrigger>
                <TabsTrigger value="evidence">Evidence</TabsTrigger>
                <TabsTrigger value="final">Final Output</TabsTrigger>
              </TabsList>
            </div>

            <TabsContent value="iterations" className="min-h-0 flex-1 mt-0">
              <ScrollArea className="h-full">
                <div className="flex flex-col gap-3 p-3">
                  {iterations.length > 0 ? (
                    <>
                      <div className="flex flex-col gap-3">
                        {iterations.map((iteration) => (
                          <IterationRow
                            key={iteration.id}
                            iteration={iteration}
                            selected={selectedIteration?.id === iteration.id}
                            onSelect={() => selectIteration(iteration.id)}
                          />
                        ))}
                      </div>
                      <IterationDetail iteration={selectedIteration} />
                    </>
                  ) : activity.length > 0 ? (
                    <div className="flex flex-col gap-3">
                      {activity.map((entry) => (
                        <Card key={entry.id} className="border-border-subtle/80 bg-muted/15">
                          <CardContent className="flex flex-col gap-2 pt-4">
                            <div className="flex flex-wrap gap-2">
                              <Badge variant="secondary">{entry.kind}</Badge>
                              {entry.iteration != null ? (
                                <Badge variant="secondary">iter {entry.iteration}</Badge>
                              ) : null}
                              {entry.phase ? (
                                <Badge variant="secondary">{entry.phase}</Badge>
                              ) : null}
                            </div>
                            <p className="text-sm text-foreground">{entry.text}</p>
                          </CardContent>
                        </Card>
                      ))}
                    </div>
                  ) : status === "bootstrapping" || status === "running" ? (
                    <div className="flex flex-col gap-3">
                      <Skeleton className="h-20 w-full rounded-xl" />
                      <Skeleton className="h-28 w-full rounded-xl" />
                      <Skeleton className="h-16 w-full rounded-xl" />
                    </div>
                  ) : (
                    <EmptyPanel
                      title="No iterations yet"
                      description="Execution summaries will appear here once the runtime starts producing observations."
                      icon={SearchSlash}
                    />
                  )}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="evidence" className="min-h-0 flex-1 mt-0">
              <ScrollArea className="h-full">
                <div className="flex flex-col gap-4 p-3">
                  {contextSources.length > 0 ? (
                    <section className="flex flex-col gap-3">
                      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Staged corpus
                      </div>
                      {contextSources.map((source) => (
                        <ContextSourceCard key={source.sourceId} source={source} />
                      ))}
                    </section>
                  ) : null}

                  {sources.length > 0 ? (
                    <section className="flex flex-col gap-3">
                      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Referenced sources
                      </div>
                      {sources.map((source) => (
                        <Card key={source.sourceId} className="border-border-subtle/80 bg-muted/15">
                          <CardHeader className="gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <CardTitle className="text-sm">{source.title}</CardTitle>
                              <Badge variant="secondary" className="capitalize">
                                {source.kind}
                              </Badge>
                            </div>
                            <CardDescription>
                              {source.displayUrl ?? source.url ?? "Local evidence"}
                            </CardDescription>
                          </CardHeader>
                          {(source.description || source.quote) && (
                            <CardContent className="flex flex-col gap-2 text-sm text-muted-foreground">
                              {source.description ? <p>{source.description}</p> : null}
                              {source.quote ? (
                                <>
                                  <Separator />
                                  <p className="text-foreground">{source.quote}</p>
                                </>
                              ) : null}
                            </CardContent>
                          )}
                        </Card>
                      ))}
                    </section>
                  ) : null}

                  {attachments.length > 0 ? (
                    <section className="flex flex-col gap-3">
                      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Attachments
                      </div>
                      {attachments.map((attachment) => (
                        <Card
                          key={attachment.attachmentId}
                          className="border-border-subtle/80 bg-muted/15"
                        >
                          <CardHeader className="gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <CardTitle className="text-sm">{attachment.name}</CardTitle>
                              {attachment.kind ? (
                                <Badge variant="secondary">{attachment.kind}</Badge>
                              ) : null}
                              {attachment.mimeType || attachment.mediaType ? (
                                <Badge variant="secondary">
                                  {attachment.mimeType ?? attachment.mediaType}
                                </Badge>
                              ) : null}
                            </div>
                            <CardDescription>
                              {attachment.description ?? "Staged workspace material"}
                            </CardDescription>
                          </CardHeader>
                        </Card>
                      ))}
                    </section>
                  ) : null}

                  {contextSources.length === 0 &&
                  sources.length === 0 &&
                  attachments.length === 0 ? (
                    <EmptyPanel
                      title="No evidence surfaced yet"
                      description="Evidence-backed files, excerpts, and staged corpus items will appear here once the run grounds its answer."
                      icon={SearchSlash}
                    />
                  ) : null}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="final" className="min-h-0 flex-1 mt-0">
              <ScrollArea className="h-full">
                <div className="p-3">
                  <ArtifactPanel artifact={finalArtifact} />
                </div>
              </ScrollArea>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
