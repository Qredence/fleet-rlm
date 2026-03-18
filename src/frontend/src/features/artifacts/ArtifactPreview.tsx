import { ScrollArea } from "@/components/ui/scroll-area";
import { SkillMarkdown } from "@/components/shared/SkillMarkdown";
import type { ExecutionStep } from "@/lib/data/artifactTypes";
import { buildArtifactPreviewModel } from "@/features/artifacts/parsers/artifactPayloadSummaries";

interface ArtifactPreviewProps {
  steps: ExecutionStep[];
  activeStepId?: string;
}

export function ArtifactPreview({ steps, activeStepId }: ArtifactPreviewProps) {
  const outputStep =
    steps.find((step) => step.id === activeStepId && step.type === "output") ??
    [...steps].reverse().find((step) => step.type === "output");

  if (!outputStep) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Final output appears here.
      </div>
    );
  }

  return (
    <div className="h-full rounded-xl border border-border-subtle/80 bg-card/45 overflow-hidden">
      <ScrollArea className="h-full">
        <div className="p-4 md:p-5">
          {(() => {
            const model = buildArtifactPreviewModel(outputStep);
            switch (model.kind) {
              case "markdown":
                return <SkillMarkdown content={model.text} />;
              case "text":
                return (
                  <pre className="text-xs text-foreground whitespace-pre-wrap overflow-wrap-break-word">
                    {model.text || "No preview output was captured for this run."}
                  </pre>
                );
              case "error":
                return (
                  <div className="rounded-md border border-red-500/40 bg-red-500/5 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-red-500">
                      Execution failed
                    </p>
                    <p className="mt-1 text-sm text-foreground whitespace-pre-wrap overflow-wrap-break-word">
                      {model.message}
                    </p>
                    {model.details && (
                      <pre className="mt-2 max-h-80 overflow-auto rounded border border-red-500/20 bg-card/60 p-2 text-xs whitespace-pre-wrap overflow-wrap-break-word">
                        {model.details}
                      </pre>
                    )}
                  </div>
                );
              case "tool_result":
                return (
                  <div className="space-y-3">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Tool Result
                      </p>
                      {model.toolName && (
                        <p className="text-sm text-foreground mt-1">{model.toolName}</p>
                      )}
                    </div>
                    {model.input && (
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                          Input
                        </p>
                        <pre className="max-h-40 overflow-auto rounded-md border border-border-subtle bg-muted/30 p-2 text-xs whitespace-pre-wrap overflow-wrap-break-word">
                          {model.input}
                        </pre>
                      </div>
                    )}
                    {model.output && (
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                          Output
                        </p>
                        <pre className="max-h-64 overflow-auto rounded-md border border-border-subtle bg-muted/30 p-2 text-xs whitespace-pre-wrap overflow-wrap-break-word">
                          {model.output}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              case "trajectory":
                return (
                  <div className="space-y-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Trajectory Summary
                    </p>
                    {model.thought && (
                      <div>
                        <p className="text-xs font-semibold text-foreground/80">Thought</p>
                        <p className="text-sm text-muted-foreground whitespace-pre-wrap overflow-wrap-break-word">
                          {model.thought}
                        </p>
                      </div>
                    )}
                    {model.action && (
                      <div>
                        <p className="text-xs font-semibold text-foreground/80">Action</p>
                        <p className="text-sm text-muted-foreground whitespace-pre-wrap overflow-wrap-break-word">
                          {model.action}
                        </p>
                      </div>
                    )}
                    {model.observation && (
                      <div>
                        <p className="text-xs font-semibold text-foreground/80">Observation</p>
                        <pre className="max-h-64 overflow-auto rounded-md border border-border-subtle bg-muted/30 p-2 text-xs whitespace-pre-wrap overflow-wrap-break-word">
                          {model.observation}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              case "json":
                return (
                  <pre className="text-xs text-foreground whitespace-pre-wrap overflow-wrap-break-word">
                    {JSON.stringify(model.value, null, 2)}
                  </pre>
                );
              case "empty":
              default:
                return (
                  <pre className="text-xs text-foreground whitespace-pre-wrap overflow-wrap-break-word">
                    No preview output was captured for this run.
                  </pre>
                );
            }
          })()}
        </div>
      </ScrollArea>
    </div>
  );
}
