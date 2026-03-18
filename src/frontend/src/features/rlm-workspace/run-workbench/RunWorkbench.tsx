import { FileQuestion, MessagesSquare, SearchSlash, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { cn } from "@/lib/utils/cn";
import { useRunWorkbenchStore } from "@/features/rlm-workspace/run-workbench/runWorkbenchStore";
import type {
  ArtifactSummary,
  CallbackSummary,
  ContextSourceSummary,
  IterationSummary,
  PromptHandleSummary,
} from "@/features/rlm-workspace/run-workbench/types";

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

function PromptHandleList({ handles }: { handles: PromptHandleSummary[] }) {
  if (handles.length === 0) {
    return (
      <EmptyPanel
        title="No prompt objects yet"
        description="Large task, observation, and history payloads will appear here once they are externalized."
        icon={MessagesSquare}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {handles.map((handle) => (
        <Card key={handle.handleId} className="border-border-subtle/80 bg-muted/15">
          <CardHeader className="gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-sm">{handle.label ?? handle.handleId}</CardTitle>
              {handle.kind ? <Badge variant="secondary">{handle.kind}</Badge> : null}
            </div>
            <CardDescription>{handle.path || "Sandbox prompt object"}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm text-muted-foreground">
            <div className="flex flex-wrap gap-3">
              {handle.charCount != null ? <span>{handle.charCount} chars</span> : null}
              {handle.lineCount != null ? <span>{handle.lineCount} lines</span> : null}
            </div>
            {handle.preview ? <p>{handle.preview}</p> : null}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ArtifactPanel({ artifact }: { artifact?: ArtifactSummary | null }) {
  if (!artifact) {
    return (
      <EmptyPanel
        title="No final output yet"
        description="The final typed DSPy output will appear here when the run completes."
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
      <pre className="max-h-96 overflow-auto rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-xs text-muted-foreground whitespace-pre-wrap overflow-wrap-break-word">
        {renderedArtifactText ?? stringifyValue(artifact.value)}
      </pre>
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
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Planner summary
            </div>
            <div className="rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-sm text-foreground">
              {iteration.reasoningSummary}
            </div>
          </section>
        ) : null}
        {iteration.code ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Executed code
            </div>
            <pre className="max-h-64 overflow-auto rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-xs text-muted-foreground whitespace-pre-wrap overflow-wrap-break-word">
              {iteration.code}
            </pre>
          </section>
        ) : null}
        {iteration.stdout ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              STDOUT
            </div>
            <pre className="max-h-56 overflow-auto rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-xs text-muted-foreground whitespace-pre-wrap overflow-wrap-break-word">
              {iteration.stdout}
            </pre>
          </section>
        ) : null}
        {iteration.stderr ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              STDERR
            </div>
            <pre className="max-h-56 overflow-auto rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-xs text-muted-foreground whitespace-pre-wrap overflow-wrap-break-word">
              {iteration.stderr}
            </pre>
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

function CallbackRow({
  callback,
  selected,
  onSelect,
}: {
  callback: CallbackSummary;
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
        <Badge variant="secondary">{callback.callbackName}</Badge>
        <Badge variant={statusBadgeVariant(callback.status)}>{callback.status}</Badge>
        {callback.iteration != null ? (
          <Badge variant="secondary">iter {callback.iteration}</Badge>
        ) : null}
      </div>
      <p className="mt-2 text-sm text-foreground">{callback.label ?? callback.task}</p>
      {callback.resultPreview ? (
        <p className="mt-2 text-xs text-muted-foreground">{callback.resultPreview}</p>
      ) : null}
    </button>
  );
}

function CallbackDetail({ callback }: { callback?: CallbackSummary | null }) {
  if (!callback) {
    return (
      <EmptyPanel
        title="No callback selected"
        description="Select a semantic subcall to inspect its task, provenance, and result preview."
        icon={SearchSlash}
      />
    );
  }

  return (
    <Card className="border-border-subtle/80 bg-card/80">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm">{callback.callbackName}</CardTitle>
          <Badge variant={statusBadgeVariant(callback.status)}>{callback.status}</Badge>
          {callback.iteration != null ? (
            <Badge variant="secondary">iter {callback.iteration}</Badge>
          ) : null}
        </div>
        <CardDescription>{callback.label ?? callback.task}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <section className="flex flex-col gap-2">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Task
          </div>
          <div className="rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-sm text-foreground">
            {callback.task}
          </div>
        </section>
        {callback.source ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Evidence provenance
            </div>
            <div className="rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-sm text-muted-foreground">
              {callback.source.path ? <div>{callback.source.path}</div> : null}
              {callback.source.startLine != null ? (
                <div>
                  lines {callback.source.startLine}
                  {callback.source.endLine != null &&
                  callback.source.endLine !== callback.source.startLine
                    ? `-${callback.source.endLine}`
                    : ""}
                </div>
              ) : null}
              {callback.source.header ? <div>header: {callback.source.header}</div> : null}
              {callback.source.pattern ? <div>pattern: {callback.source.pattern}</div> : null}
              {callback.source.preview ? (
                <p className="mt-2 text-foreground">{callback.source.preview}</p>
              ) : null}
            </div>
          </section>
        ) : null}
        {callback.resultPreview ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Result preview
            </div>
            <div className="rounded-xl border border-border-subtle/80 bg-muted/15 p-3 text-sm text-foreground">
              {callback.resultPreview}
            </div>
          </section>
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
    selectedCallbackId,
    selectedTab,
    selectTab,
    selectIteration,
    selectCallback,
    finalArtifact,
    summary,
    errorMessage,
  } = useRunWorkbenchStore();

  const selectedIteration =
    iterations.find((item) => item.id === selectedIterationId) ?? iterations.at(-1) ?? null;
  const selectedCallback =
    callbacks.find((item) => item.id === selectedCallbackId) ?? callbacks.at(-1) ?? null;
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

      <Card className="shrink-0 border-border-subtle/80 bg-card/80">
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Daytona analyst workspace</Badge>
            <Badge variant={statusBadgeVariant(status)}>{status}</Badge>
            {summary?.terminationReason ? (
              <Badge variant="secondary">{summary.terminationReason}</Badge>
            ) : null}
          </div>
          <CardTitle className="text-sm leading-6">{task ?? "Corpus-grounded analysis"}</CardTitle>
          <CardDescription>
            {repoUrl ? repoUrl : "No repository configured"}
            {repoRef ? ` @ ${repoRef}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{iterations.length} iterations</span>
          <span>{callbacks.length} callbacks</span>
          <span>{promptHandles.length} prompt objects</span>
          <span>{sources.length + attachments.length} evidence items</span>
          {summary?.durationMs != null ? <span>{summary.durationMs}ms</span> : null}
        </CardContent>
      </Card>

      <Card className="flex min-h-0 flex-1 flex-col" data-testid="detail-tabs">
        <CardContent className="min-h-0 flex-1 pb-4">
          <Tabs
            className="flex h-full min-h-0 flex-col gap-3"
            value={selectedTab}
            onValueChange={(value) =>
              selectTab(value as "iterations" | "evidence" | "callbacks" | "prompts" | "final")
            }
          >
            <TabsList className="grid h-auto w-full grid-cols-2 gap-1 rounded-xl border border-border-subtle/70 bg-card/70 p-1 sm:grid-cols-5">
              <TabsTrigger value="iterations" className="px-3 py-2 text-xs sm:text-sm">
                Iterations
              </TabsTrigger>
              <TabsTrigger value="evidence" className="px-3 py-2 text-xs sm:text-sm">
                Evidence
              </TabsTrigger>
              <TabsTrigger value="callbacks" className="px-3 py-2 text-xs sm:text-sm">
                Callbacks
              </TabsTrigger>
              <TabsTrigger value="prompts" className="px-3 py-2 text-xs sm:text-sm">
                Prompts
              </TabsTrigger>
              <TabsTrigger value="final" className="px-3 py-2 text-xs sm:text-sm">
                Final
              </TabsTrigger>
            </TabsList>

            <TabsContent value="iterations" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <div className="flex flex-col gap-4">
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
                      description="Iteration summaries will appear here once the Daytona host loop starts producing observations."
                      icon={SearchSlash}
                    />
                  )}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="evidence" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <div className="flex flex-col gap-4">
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

            <TabsContent value="callbacks" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <div className="flex flex-col gap-4">
                  {callbacks.length > 0 ? (
                    <>
                      <div className="flex flex-col gap-3">
                        {callbacks.map((callback) => (
                          <CallbackRow
                            key={callback.id}
                            callback={callback}
                            selected={selectedCallback?.id === callback.id}
                            onSelect={() => selectCallback(callback.id)}
                          />
                        ))}
                      </div>
                      <CallbackDetail callback={selectedCallback} />
                    </>
                  ) : (
                    <EmptyPanel
                      title="No semantic callbacks yet"
                      description="`llm_query(...)` and `llm_query_batched(...)` activity will appear here when the run delegates semantic work."
                      icon={MessagesSquare}
                    />
                  )}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="prompts" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <PromptHandleList handles={promptHandles} />
              </ScrollArea>
            </TabsContent>

            <TabsContent value="final" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <ArtifactPanel artifact={finalArtifact} />
              </ScrollArea>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
