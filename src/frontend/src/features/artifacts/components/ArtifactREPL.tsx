import { useCodeMirror } from "@/hooks/useCodeMirror";

import type { ExecutionStep } from "@/stores/artifactStore";
import {
  Sandbox,
  SandboxHeader,
  SandboxContent,
  SandboxTabs,
  SandboxTabsBar,
  SandboxTabsList,
  SandboxTabsTrigger,
  SandboxTabContent,
} from "@/components/ai-elements/sandbox";
import { ToolOutput } from "@/components/ai-elements/tool";
import type { ToolState } from "@/components/ai-elements/tool";

// ── Read-only CodeMirror code viewer ───────────────────────────────

interface CodeViewerProps {
  code: string;
}

function CodeViewer({ code }: CodeViewerProps) {
  const { containerRef } = useCodeMirror({ value: code, readOnly: true });
  return (
    <div
      ref={containerRef}
      className="text-xs font-mono overflow-auto min-h-50"
    />
  );
}

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
  if (!value) return "";

  if (typeof value === "object" && !Array.isArray(value)) {
    const rec = value as Record<string, unknown>;
    const extracted = rec.text ?? rec.output ?? rec.result ?? rec.message;
    if (typeof extracted === "string") return extracted;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function resolveCode(step: ExecutionStep | undefined): string {
  if (!step) return "";

  let inputObj: unknown = step.input;
  if (typeof step.input === "string") {
    try {
      const parsed = JSON.parse(step.input);
      if (parsed && typeof parsed === "object") inputObj = parsed;
    } catch {
      // Not JSON
    }
  }

  if (typeof inputObj === "string") return inputObj;

  const input = asRecord(inputObj);
  return asText(
    input?.code ?? input?.tool_input ?? input?.prompt ?? step.input,
  );
}

function resolveVariables(
  step: ExecutionStep | undefined,
): Record<string, unknown> | undefined {
  if (!step) return undefined;

  let outputObj: unknown = step.output;
  if (typeof step.output === "string") {
    try {
      const parsed = JSON.parse(step.output);
      if (parsed && typeof parsed === "object") outputObj = parsed;
    } catch {
      // Not JSON
    }
  }

  const output = asRecord(outputObj);
  const vars = asRecord(output?.variables);
  if (vars) return vars;
  return asRecord(output?.locals);
}

function resolveOutput(step: ExecutionStep | undefined): string {
  if (!step) return "";

  let outputObj: unknown = step.output;
  if (typeof step.output === "string") {
    try {
      const parsed = JSON.parse(step.output);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        outputObj = parsed;
      }
    } catch {
      // Not JSON
    }
  }

  const outputRecord = asRecord(outputObj);
  if (outputRecord) {
    const extracted =
      outputRecord.tool_output ?? outputRecord.result ?? outputRecord.text;

    if (extracted !== undefined) {
      if (typeof extracted === "string") return extracted;
      return JSON.stringify(extracted, null, 2);
    }
  }

  if (typeof outputObj === "string") return outputObj;
  try {
    return JSON.stringify(outputObj, null, 2);
  } catch {
    return String(outputObj);
  }
}

function inferToolState(step: ExecutionStep | undefined): ToolState {
  if (!step) return "running";
  const label = step.label?.toLowerCase() ?? "";
  if (label.includes("error")) return "output-error";
  if (step.output != null) return "output-available";
  return "running";
}

export function ArtifactREPL({ steps, activeStepId }: ArtifactReplProps) {
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
      <div className="h-full flex flex-col items-center justify-center gap-3 text-center px-6">
        <p className="text-sm text-muted-foreground">
          REPL data streams in when the agent calls a code-execution tool (e.g.{" "}
          <code className="font-mono bg-muted rounded px-1">python_repl</code>,{" "}
          <code className="font-mono bg-muted rounded px-1">bash</code>).
        </p>
        <p className="text-xs text-muted-foreground">
          Send a prompt that requires running code to see the code, variables,
          and output here.
        </p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col gap-3">
      {/* Code editor section */}
      <Sandbox defaultOpen>
        <SandboxHeader
          title={target.label || "Code"}
          state={inferToolState(target)}
        />
        <SandboxContent>
          <SandboxTabs defaultValue="code">
            <SandboxTabsBar>
              <SandboxTabsList>
                <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
              </SandboxTabsList>
            </SandboxTabsBar>

            <SandboxTabContent value="code">
              <div className="rounded-card border border-border-subtle overflow-hidden min-h-45">
                <CodeViewer
                  code={code || "# No executable code captured for this step"}
                />
              </div>
            </SandboxTabContent>

            <SandboxTabContent value="output">
              <ToolOutput
                output={
                  <pre className="text-xs text-foreground whitespace-pre-wrap break-words">
                    {output || "No output captured."}
                  </pre>
                }
                errorText={
                  target.label?.toLowerCase().includes("error")
                    ? output
                    : undefined
                }
              />
            </SandboxTabContent>
          </SandboxTabs>
        </SandboxContent>
      </Sandbox>

      {/* Variables section */}
      {variables && (
        <div className="rounded-card border border-border-subtle p-3 overflow-auto">
          <p className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
            Variables
          </p>
          <pre className="text-xs text-foreground whitespace-pre-wrap break-words">
            {JSON.stringify(variables, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
