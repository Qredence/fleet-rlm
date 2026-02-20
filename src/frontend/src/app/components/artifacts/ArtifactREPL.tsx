import Editor from "@monaco-editor/react";

import type { ExecutionStep } from "../../stores/artifactStore";
import { useNavigation } from "../hooks/useNavigation";

interface ArtifactReplProps {
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

function resolveCode(step: ExecutionStep | undefined): string {
  if (!step) return "";
  if (typeof step.input === "string") return step.input;

  const input = asRecord(step.input);
  return asText(
    input?.code ?? input?.tool_input ?? input?.prompt ?? step.input,
  );
}

function resolveVariables(
  step: ExecutionStep | undefined,
): Record<string, unknown> | undefined {
  if (!step) return undefined;
  const output = asRecord(step.output);
  const vars = asRecord(output?.variables);
  if (vars) return vars;
  return asRecord(output?.locals);
}

function resolveOutput(step: ExecutionStep | undefined): string {
  if (!step) return "";
  if (typeof step.output === "string") return step.output;

  const output = asRecord(step.output);
  return asText(output?.tool_output ?? output?.result ?? step.output);
}

export function ArtifactREPL({ steps, activeStepId }: ArtifactReplProps) {
  const { isDark } = useNavigation();
  const target =
    steps.find((step) => step.id === activeStepId) ??
    [...steps]
      .reverse()
      .find((step) => step.type === "repl" || step.type === "tool");

  const code = resolveCode(target);
  const variables = resolveVariables(target);
  const output = resolveOutput(target);

  if (!target) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        REPL state appears once tool/repl events are streamed.
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col gap-3">
      <div className="rounded-card border border-border-subtle overflow-hidden min-h-[220px]">
        <Editor
          language="python"
          value={code || "# No executable code captured for this step"}
          theme={isDark ? "vs-dark" : "light"}
          options={{
            readOnly: true,
            minimap: { enabled: false },
            lineNumbers: "on",
            fontSize: 13,
            scrollBeyondLastLine: false,
            wordWrap: "on",
          }}
          height="240px"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 flex-1 min-h-0">
        <div className="rounded-card border border-border-subtle p-3 overflow-auto">
          <p className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
            Variables
          </p>
          <pre className="text-xs text-foreground whitespace-pre-wrap break-words">
            {variables
              ? JSON.stringify(variables, null, 2)
              : "No variables captured."}
          </pre>
        </div>
        <div className="rounded-card border border-border-subtle p-3 overflow-auto">
          <p className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
            Output
          </p>
          <pre className="text-xs text-foreground whitespace-pre-wrap break-words">
            {output || "No output captured."}
          </pre>
        </div>
      </div>
    </div>
  );
}
