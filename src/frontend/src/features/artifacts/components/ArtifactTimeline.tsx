import { Brain, Terminal, Wrench, Database, FileOutput } from "lucide-react";

import type { ArtifactStepType, ExecutionStep } from "@/stores/artifactStore";
import { cn } from "@/lib/utils/cn";
import { summarizeArtifactStep } from "@/features/artifacts/parsers/artifactPayloadSummaries";
import {
  ChainOfThought,
  ChainOfThoughtHeader,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";

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

function mapStatus(
  step: ExecutionStep,
): "pending" | "active" | "complete" | "error" {
  const label = step.label?.toLowerCase() ?? "";
  if (label.includes("error") || label.includes("failed")) return "error";
  // If the step has output, treat as complete
  if (step.output != null) return "complete";
  return "active";
}

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
      <ChainOfThought defaultOpen>
        <ChainOfThoughtHeader>Execution trace</ChainOfThoughtHeader>
        <ChainOfThoughtContent>
          {ordered.map((step) => {
            const Icon = ICONS[step.type] ?? Brain;
            const selected = step.id === activeStepId;
            const summary = summarizeArtifactStep(step);

            return (
              <button
                type="button"
                key={step.id}
                onClick={() => onSelectStep(step.id)}
                className={cn(
                  "w-full text-left transition-colors",
                  selected && "bg-accent/10 rounded-md",
                )}
              >
                <ChainOfThoughtStep
                  label={step.label}
                  status={mapStatus(step)}
                  icon={Icon}
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs text-muted-foreground whitespace-pre-wrap wrap-break-word flex-1">
                      {summary}
                    </p>
                    <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums">
                      {formatTimestamp(step.timestamp)}
                    </span>
                  </div>
                </ChainOfThoughtStep>
              </button>
            );
          })}
        </ChainOfThoughtContent>
      </ChainOfThought>
    </div>
  );
}
