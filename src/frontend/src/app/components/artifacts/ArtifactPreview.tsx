import { ScrollArea } from "../ui/scroll-area";
import { SkillMarkdown } from "../shared/SkillMarkdown";
import type { ExecutionStep } from "../../stores/artifactStore";

interface ArtifactPreviewProps {
  steps: ExecutionStep[];
  activeStepId?: string;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  return value as Record<string, unknown>;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function looksLikeMarkdown(value: string): boolean {
  return (
    /^#{1,6}\s/m.test(value) ||
    /^[-*+]\s/m.test(value) ||
    /^\d+\.\s/m.test(value) ||
    /```/.test(value) ||
    /\[[^\]]+\]\([^)]+\)/.test(value)
  );
}

function tryParseJson(value: string): unknown | undefined {
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

function resolvePreviewContent(step: ExecutionStep | undefined): unknown {
  if (!step) return undefined;
  const output = asRecord(step.output);
  return output?.text ?? output?.payload ?? step.output ?? step.input;
}

export function ArtifactPreview({ steps, activeStepId }: ArtifactPreviewProps) {
  const outputStep =
    steps.find((step) => step.id === activeStepId && step.type === "output") ??
    [...steps].reverse().find((step) => step.type === "output");

  const content = resolvePreviewContent(outputStep);
  const text = asText(content).trim();
  const parsed =
    typeof content === "string" ? tryParseJson(content) : undefined;

  if (!outputStep) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Final artifact output will appear here when execution completes.
      </div>
    );
  }

  return (
    <div className="h-full rounded-card border border-border-subtle overflow-hidden">
      <ScrollArea className="h-full">
        <div className="p-4 md:p-5">
          {parsed !== undefined && (
            <pre className="text-xs text-foreground whitespace-pre-wrap break-words">
              {JSON.stringify(parsed, null, 2)}
            </pre>
          )}

          {parsed === undefined && looksLikeMarkdown(text) && (
            <SkillMarkdown content={text} />
          )}

          {parsed === undefined && !looksLikeMarkdown(text) && (
            <pre className="text-xs text-foreground whitespace-pre-wrap break-words">
              {text || "No preview output was captured for this run."}
            </pre>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
