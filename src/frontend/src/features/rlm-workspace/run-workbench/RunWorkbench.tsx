import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils/cn";
import { buildSourceStateLabel } from "@/lib/utils/sourceContext";
import { useRunWorkbenchStore } from "@/features/rlm-workspace/run-workbench/runWorkbenchStore";
import type {
  ArtifactSummary,
  ContextSourceSummary,
  PromptHandleSummary,
  RunNode,
  TimelineEntry,
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

function nodeStatusVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "completed") return "default";
  if (status === "error") return "destructive";
  if (status === "cancel_failed") return "destructive";
  if (status === "cancelling") return "outline";
  if (status === "cancelled") return "secondary";
  return "outline";
}

function runStatusVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "completed") return "default";
  if (status === "error") return "destructive";
  if (status === "cancelling") return "outline";
  if (status === "cancelled") return "secondary";
  if (status === "bootstrapping") return "secondary";
  return "outline";
}

function buildTreeOrder(
  nodes: Record<string, RunNode>,
  rootId?: string,
): string[] {
  const childMap = new Map<string | null, string[]>();
  for (const node of Object.values(nodes)) {
    const parentKey = node.parentId ?? null;
    const bucket = childMap.get(parentKey) ?? [];
    bucket.push(node.nodeId);
    childMap.set(parentKey, bucket);
  }

  for (const bucket of childMap.values()) {
    bucket.sort((left, right) => {
      const a = nodes[left];
      const b = nodes[right];
      if (!a || !b) return left.localeCompare(right);
      if (a.depth !== b.depth) return a.depth - b.depth;
      return left.localeCompare(right);
    });
  }

  const order: string[] = [];
  const visit = (nodeId: string) => {
    order.push(nodeId);
    for (const childId of childMap.get(nodeId) ?? []) {
      visit(childId);
    }
  };

  if (rootId && nodes[rootId]) {
    visit(rootId);
  }

  for (const nodeId of Object.keys(nodes).sort()) {
    if (!order.includes(nodeId) && (nodes[nodeId]?.parentId == null || !nodes[nodes[nodeId]!.parentId!])) {
      visit(nodeId);
    }
  }

  return order;
}

function PromptHandleList({
  handles,
}: {
  handles: PromptHandleSummary[];
}) {
  if (handles.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No prompt objects have been surfaced for this node yet.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {handles.map((handle) => (
        <div
          key={handle.handleId}
          className="rounded-xl border border-border-subtle/80 bg-muted/20 p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">
              {handle.label ?? handle.handleId}
            </span>
            {handle.kind ? <Badge variant="outline">{handle.kind}</Badge> : null}
          </div>
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
            {handle.path ? <span>{handle.path}</span> : null}
            {handle.charCount != null ? <span>{handle.charCount} chars</span> : null}
            {handle.lineCount != null ? <span>{handle.lineCount} lines</span> : null}
          </div>
          {handle.preview ? (
            <p className="mt-2 text-sm text-muted-foreground">{handle.preview}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function ArtifactPanel({ artifact }: { artifact?: ArtifactSummary | null }) {
  if (!artifact) {
    return (
      <p className="text-sm text-muted-foreground">
        The final artifact has not been produced yet.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {artifact.kind ? <Badge variant="outline">{artifact.kind}</Badge> : null}
        {artifact.finalizationMode ? (
          <Badge variant="secondary">{artifact.finalizationMode}</Badge>
        ) : null}
        {artifact.variableName ? (
          <Badge variant="outline">var {artifact.variableName}</Badge>
        ) : null}
      </div>
      {artifact.textPreview ? (
        <div className="rounded-xl border border-border-subtle/80 bg-muted/20 p-3">
          <p className="text-sm text-foreground">{artifact.textPreview}</p>
        </div>
      ) : null}
      <pre className="max-h-96 overflow-auto rounded-xl border border-border-subtle/80 bg-muted/20 p-3 text-xs text-muted-foreground whitespace-pre-wrap break-words">
        {stringifyValue(artifact.value)}
      </pre>
    </div>
  );
}

function SourceList({
  repoUrl,
  repoRef,
  contextSources,
}: {
  repoUrl?: string;
  repoRef?: string | null;
  contextSources: ContextSourceSummary[];
}) {
  if (!repoUrl && contextSources.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No external sources</p>
    );
  }

  return (
    <div className="space-y-3">
      {repoUrl ? (
        <div className="rounded-xl border border-border-subtle/80 bg-muted/20 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Repo</Badge>
            {repoRef ? <Badge variant="outline">Ref {repoRef}</Badge> : null}
          </div>
          <p className="mt-2 text-sm font-medium text-foreground break-all">
            {repoUrl}
          </p>
        </div>
      ) : null}

      {contextSources.map((source) => (
        <div
          key={source.sourceId}
          className="rounded-xl border border-border-subtle/80 bg-muted/20 p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">
              {source.kind === "directory" ? "Directory" : "Local context"}
            </Badge>
            {source.fileCount != null ? (
              <Badge variant="outline">{source.fileCount} files</Badge>
            ) : null}
            {source.skippedCount ? (
              <Badge variant="outline">{source.skippedCount} skipped</Badge>
            ) : null}
          </div>
          <p className="mt-2 text-sm font-medium text-foreground break-all">
            {source.hostPath}
          </p>
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
            {source.sourceType ? <span>{source.sourceType}</span> : null}
            {source.extractionMethod ? <span>{source.extractionMethod}</span> : null}
            {source.stagedPath ? <span>{source.stagedPath}</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function TimelineRow({
  entry,
  isSelected,
  onSelect,
}: {
  entry: TimelineEntry;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-xl border px-3 py-3 text-left transition-colors",
        isSelected
          ? "border-accent bg-accent/10"
          : "border-border-subtle/80 bg-muted/15 hover:bg-muted/35",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{entry.kind}</Badge>
        {entry.phase ? <Badge variant="secondary">{entry.phase}</Badge> : null}
        {entry.status ? <Badge variant="outline">{entry.status}</Badge> : null}
        {entry.depth != null ? (
          <Badge variant="outline">depth {entry.depth}</Badge>
        ) : null}
      </div>
      <p className="mt-2 text-sm text-foreground">{entry.text}</p>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {entry.nodeId ? <span>node {entry.nodeId.slice(0, 8)}</span> : null}
        {entry.sandboxId ? <span>sandbox {entry.sandboxId.slice(0, 8)}</span> : null}
        {entry.promptHandleCount != null ? (
          <span>{entry.promptHandleCount} prompt objects</span>
        ) : null}
        {entry.timestamp ? <span>{entry.timestamp}</span> : null}
      </div>
      {entry.artifactPreview ? (
        <p className="mt-2 text-xs text-muted-foreground">{entry.artifactPreview}</p>
      ) : null}
      {entry.warning ? (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{entry.warning}</p>
      ) : null}
    </button>
  );
}

export function RunWorkbench() {
  const {
    status,
    runId,
    repoUrl,
    repoRef,
    daytonaMode,
    task,
    contextSources,
    rootId,
    nodes,
    timeline,
    selectedNodeId,
    selectNode,
    selectedTab,
    selectTab,
    finalArtifact,
    summary,
    errorMessage,
  } = useRunWorkbenchStore();

  const treeOrder = buildTreeOrder(nodes, rootId);
  const firstTreeNodeId = treeOrder[0];
  const selectedNode =
    (selectedNodeId ? nodes[selectedNodeId] : undefined) ??
    (rootId ? nodes[rootId] : undefined) ??
    (firstTreeNodeId ? nodes[firstTreeNodeId] : undefined);
  const sourceStateLabel = buildSourceStateLabel({
    hasRepo: Boolean(repoUrl),
    hasContext: contextSources.length > 0,
  });

  return (
    <div
      className="flex h-full min-h-0 flex-1 flex-col gap-4 overflow-hidden"
      data-testid="run-workbench"
    >
      <Card className="shrink-0">
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">Run Workbench</CardTitle>
            <Badge variant={runStatusVariant(status)}>{status}</Badge>
            <Badge variant="secondary">{sourceStateLabel}</Badge>
            {daytonaMode ? <Badge variant="outline">{daytonaMode}</Badge> : null}
            {summary?.terminationReason ? (
              <Badge variant="outline">{summary.terminationReason}</Badge>
            ) : null}
          </div>
          <CardDescription>
            Live recursive workspace details, grounded in metadata and previews.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              External sources
            </p>
            <div className="pt-1">
              <SourceList
                repoUrl={repoUrl}
                repoRef={repoRef}
                contextSources={contextSources}
              />
            </div>
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>{Object.keys(nodes).length} nodes</span>
              {summary?.durationMs != null ? <span>{summary.durationMs} ms</span> : null}
            </div>
          </div>
          <Separator />
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Task
            </p>
            <p className="text-sm text-foreground">
              {task ?? "Send a task to begin."}
            </p>
            {runId ? (
              <p className="text-xs text-muted-foreground break-all">{runId}</p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {errorMessage ? (
        <div className="shrink-0 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {errorMessage}
        </div>
      ) : null}

      {summary?.warnings?.length ? (
        <div className="shrink-0 rounded-xl border border-amber-400/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 dark:text-amber-100">
          <p className="font-medium">Cancellation warnings</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {summary.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <Card
        className="flex min-h-0 flex-[0.95] flex-col"
        data-testid="run-tree"
      >
        <CardHeader className="gap-2">
          <CardTitle className="text-base">Run tree</CardTitle>
          <CardDescription>
            Recursive nodes, child links, and status transitions.
          </CardDescription>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 pb-4">
          <ScrollArea className="h-full pr-2">
            <div className="space-y-2">
              {treeOrder.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Start a run to see the recursive node tree.
                </p>
              ) : (
                treeOrder.map((nodeId) => {
                  const node = nodes[nodeId];
                  if (!node) return null;
                  return (
                    <button
                      key={node.nodeId}
                      type="button"
                      onClick={() => selectNode(node.nodeId)}
                      className={cn(
                        "w-full rounded-xl border px-3 py-3 text-left transition-colors",
                        selectedNode?.nodeId === node.nodeId
                          ? "border-accent bg-accent/10"
                          : "border-border-subtle/80 bg-muted/15 hover:bg-muted/35",
                      )}
                      style={{ marginLeft: `${node.depth * 10}px` }}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium text-foreground">
                          {node.task}
                        </span>
                        <Badge variant={nodeStatusVariant(node.status)}>
                          {node.status}
                        </Badge>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                        <span>depth {node.depth}</span>
                        <span>{node.childLinks.length} child links</span>
                        {node.promptHandles.length > 0 ? (
                          <span>{node.promptHandles.length} prompts</span>
                        ) : null}
                        {node.warnings?.length ? (
                          <span>{node.warnings.length} warnings</span>
                        ) : null}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      <Card
        className="flex min-h-0 flex-[1.15] flex-col"
        data-testid="detail-tabs"
      >
        <CardHeader className="gap-2">
          <CardTitle className="text-base">Details</CardTitle>
          <CardDescription>
            Timeline, selected node context, prompt objects, and the final artifact.
          </CardDescription>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 pb-4">
          <Tabs
            className="flex h-full min-h-0 flex-col"
            value={selectedTab}
            onValueChange={(value) =>
              selectTab(value as "timeline" | "node" | "prompts" | "final")
            }
          >
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
              <TabsTrigger value="node">Node</TabsTrigger>
              <TabsTrigger value="prompts">Prompts</TabsTrigger>
              <TabsTrigger value="final">Final</TabsTrigger>
            </TabsList>

            <TabsContent value="timeline" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <div className="space-y-3">
                  {timeline.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      Timeline events will appear here during the run.
                    </p>
                  ) : (
                    timeline.map((entry) => (
                      <TimelineRow
                        key={entry.id}
                        entry={entry}
                        isSelected={
                          selectedNode?.nodeId != null &&
                          entry.nodeId === selectedNode.nodeId
                        }
                        onSelect={() => {
                          if (entry.nodeId) selectNode(entry.nodeId);
                        }}
                      />
                    ))
                  )}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="node" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                {selectedNode ? (
                  <div className="space-y-4">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground">
                          {selectedNode.task}
                        </h3>
                        <Badge variant={nodeStatusVariant(selectedNode.status)}>
                          {selectedNode.status}
                        </Badge>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                        <span>node {selectedNode.nodeId}</span>
                        {selectedNode.parentId ? (
                          <span>parent {selectedNode.parentId}</span>
                        ) : null}
                        <span>depth {selectedNode.depth}</span>
                        {selectedNode.sandboxId ? (
                          <span>sandbox {selectedNode.sandboxId}</span>
                        ) : null}
                      </div>
                    </div>

                    {selectedNode.workspacePath ? (
                      <>
                        <Separator />
                        <div>
                          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            Workspace
                          </p>
                          <p className="mt-2 text-sm text-foreground break-all">
                            {selectedNode.workspacePath}
                          </p>
                        </div>
                      </>
                    ) : null}

                    {selectedNode.warnings?.length ? (
                      <>
                        <Separator />
                        <div className="space-y-2">
                          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            Warnings
                          </p>
                          <div className="rounded-xl border border-amber-400/40 bg-amber-500/10 p-3">
                            <ul className="list-disc space-y-1 pl-5 text-sm text-amber-900 dark:text-amber-100">
                              {selectedNode.warnings.map((warning) => (
                                <li key={warning}>{warning}</li>
                              ))}
                            </ul>
                          </div>
                        </div>
                      </>
                    ) : null}

                    {selectedNode.childLinks.length > 0 ? (
                      <>
                        <Separator />
                        <div className="space-y-3">
                          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            Child links
                          </p>
                          {selectedNode.childLinks.map(
                            (
                              link: RunNode["childLinks"][number],
                              index: number,
                            ) => (
                              <div
                                key={`${link.callbackName}-${index}-${link.childId ?? "none"}`}
                                className="rounded-xl border border-border-subtle/80 bg-muted/20 p-3"
                              >
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="outline">{link.callbackName}</Badge>
                                  <Badge variant="secondary">{link.status}</Badge>
                                </div>
                                <p className="mt-2 text-sm text-foreground">
                                  {link.task.label ?? link.task.task}
                                </p>
                                {link.task.source?.path ? (
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {link.task.source.path}
                                    {link.task.source.startLine != null
                                      ? `:${link.task.source.startLine}`
                                      : ""}
                                    {link.task.source.endLine != null &&
                                    link.task.source.endLine !==
                                      link.task.source.startLine
                                      ? `-${link.task.source.endLine}`
                                      : ""}
                                  </p>
                                ) : null}
                                {link.resultPreview ? (
                                  <p className="mt-2 text-xs text-muted-foreground">
                                    {link.resultPreview}
                                  </p>
                                ) : null}
                              </div>
                            ),
                          )}
                        </div>
                      </>
                    ) : null}

                    {selectedNode.finalArtifact ? (
                      <>
                        <Separator />
                        <ArtifactPanel artifact={selectedNode.finalArtifact} />
                      </>
                    ) : null}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Select a node to inspect its details.
                  </p>
                )}
              </ScrollArea>
            </TabsContent>

            <TabsContent value="prompts" className="min-h-0 flex-1">
              <ScrollArea className="h-full pr-2">
                <PromptHandleList handles={selectedNode?.promptHandles ?? []} />
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
