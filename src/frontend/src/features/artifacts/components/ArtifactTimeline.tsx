import { Brain, Terminal, Wrench, Database, FileOutput } from "lucide-react";

import type { ArtifactStepType, ExecutionStep } from "@/stores/artifactStore";
import { cn } from "@/components/ui/utils";
import { summarizeArtifactStep } from "@/features/artifacts/parsers/artifactPayloadSummaries";

interface ArtifactTimelineProps {
  steps: ExecutionStep[];
  activeStepId?: string;
  onSelectStep: (id: string) => void;
}

const ICONS: Record<ArtifactStepType, typeof Brain> = {
  llm: Brain,
  repl: Terminal,
  tool: Wrench,
  memory: Database,
  output: FileOutput,
};

function formatTimestamp(epochMs: number): string {
  return new Date(epochMs).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function ArtifactTimeline({
  steps,
  activeStepId,
  onSelectStep,
}: ArtifactTimelineProps) {
  const ordered = [...steps].sort((a, b) => a.timestamp - b.timestamp);

  if (ordered.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Timeline will appear as execution steps stream in.
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto pr-1">
      <div className="space-y-2">
        {ordered.map((step) => {
          const Icon = ICONS[step.type] ?? Brain;
          const selected = step.id === activeStepId;

          return (
            <button
              type="button"
              key={step.id}
              onClick={() => onSelectStep(step.id)}
              className={cn(
                "w-full text-left rounded-card border p-3 transition-colors",
                selected
                  ? "border-accent bg-accent/10"
                  : "border-border-subtle hover:bg-muted/40",
              )}
            >
              <div className="flex items-start gap-3">
                <div className="w-7 h-7 rounded-md bg-muted flex items-center justify-center shrink-0">
                  <Icon className="size-4 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm text-foreground truncate">
                      {step.label}
                    </p>
                    <span className="text-xs text-muted-foreground shrink-0">
                      {formatTimestamp(step.timestamp)}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap break-words">
                    {summarizeArtifactStep(step)}
                  </p>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
