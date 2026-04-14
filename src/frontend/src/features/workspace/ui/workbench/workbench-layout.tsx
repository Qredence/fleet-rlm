import { SearchSlash, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyPanel } from "@/components/product/empty-panel";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRunWorkbenchStore } from "@/features/workspace/use-workspace";
import { ArtifactPanel, IterationDetail, ContextSourceCard } from "./workbench-detail-panel";
import { IterationRow, humanizeKind } from "./workbench-artifact-list";

export function RunWorkbench() {
  const {
    status,
    contextSources,
    iterations,
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
  const humanReview = summary?.humanReview;

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
          <TriangleAlert />
          <AlertTitle>Analysis warnings</AlertTitle>
          <AlertDescription>
            <ul className="mt-2 list-disc pl-5">
              {(summary?.warnings ?? []).map((warning, index) => (
                <li key={`summary-warning-${index}`}>{warning}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      {humanReview?.required ? (
        <Alert className="shrink-0 border-accent/25 bg-accent/5 text-foreground">
          <TriangleAlert />
          <AlertTitle>Human review needed</AlertTitle>
          <AlertDescription>
            <div className="space-y-2">
              {humanReview.reason ? <p>{humanReview.reason}</p> : null}
              {humanReview.repairTarget ? (
                <p>
                  <span className="font-medium">Review target:</span> {humanReview.repairTarget}
                </p>
              ) : null}
              {humanReview.repairSteps?.length ? (
                <ul className="list-disc pl-5">
                  {humanReview.repairSteps.map((step, index) => (
                    <li key={`human-review-step-${index}`}>{step}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      <Card className="flex min-h-0 flex-1 flex-col" data-testid="detail-tabs">
        <CardContent className="min-h-0 flex-1 p-0">
          <Tabs
            className="flex h-full min-h-0 flex-col"
            value={renderedSelectedTab}
            onValueChange={(value) => selectTab(value as "iterations" | "evidence" | "final")}
          >
            <div className="shrink-0 overflow-x-auto border-b border-border-subtle/70 px-3 no-scrollbar">
              <TabsList variant="line">
                <TabsTrigger value="final">Output</TabsTrigger>
                <TabsTrigger value="iterations">Iterations</TabsTrigger>
                <TabsTrigger value="evidence">Context</TabsTrigger>
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
                              <Badge variant="secondary">{humanizeKind(entry.kind)}</Badge>
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
