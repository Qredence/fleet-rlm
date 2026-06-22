import { memo } from "react";
import {
  CodeBlockViewer as CodeBlock,
  CodeBlockActions,
  CodeBlockCopyButton,
  CodeBlockFilename,
  CodeBlockHeader,
  CodeBlockTitle,
} from "@/components/ui/code-block";
import type { TimelineStep, StepState } from "../types/timeline";
import { useToolComplete } from "../hooks/use-tool-complete";
import { mapToolInvocationToStep, mapToolStateToStepState } from "../utils/tool-adapters";
import { ToolApprovalFooter, type ToolApproval } from "./tool-approval-footer";
import { ToolGroup } from "./tool-group";
import { ToolRowBase } from "./tool-row-base";
import {
  cleanAndFormatSnippet,
  extractCommandSummary,
  summarizeField,
  inferCodeLanguage,
} from "../utils/code-repair";

function ToolCodeBlock({
  code,
  language,
  filename,
  showLineNumbers,
  className,
}: {
  code: string;
  language: string;
  filename: string;
  showLineNumbers?: boolean;
  className?: string;
}) {
  return (
    <CodeBlock
      code={code}
      language={language}
      showLineNumbers={showLineNumbers}
      className={className}
    >
      <CodeBlockHeader>
        <CodeBlockTitle>
          <CodeBlockFilename>{filename}</CodeBlockFilename>
        </CodeBlockTitle>
        <CodeBlockActions>
          <CodeBlockCopyButton code={code} />
        </CodeBlockActions>
      </CodeBlockHeader>
    </CodeBlock>
  );
}

function NestedCodeRow({
  label,
  subtitle,
  code,
  language,
  filename,
  defaultOpen = true,
}: {
  label: string;
  subtitle?: string;
  code: string;
  language: string;
  filename: string;
  defaultOpen?: boolean;
}) {
  const formatted = cleanAndFormatSnippet(code);
  if (!formatted.trim()) return null;

  return (
    <ToolRowBase
      completeLabel={label}
      isAnimating={false}
      subtitle={subtitle}
      expandable
      defaultOpen={defaultOpen}
    >
      <ToolCodeBlock
        code={formatted}
        filename={filename}
        language={language}
        showLineNumbers={formatted.includes("\n")}
        className="border-border/60 bg-background"
      />
    </ToolRowBase>
  );
}

export type BashToolTerminalCardProps = {
  step: Extract<TimelineStep, { type: "tool-call" }>;
  state: StepState;
  onComplete: () => void;
  approval?: ToolApproval;
};

export function BashToolTerminalCard({
  step,
  state,
  onComplete,
  approval,
}: BashToolTerminalCardProps) {
  useToolComplete(state === "animating", step.duration, onComplete);
  const isPending = state === "animating";
  const command = step.bashCommand ?? step.toolDetail ?? "";
  const formattedCommand = cleanAndFormatSnippet(command);
  const summary = extractCommandSummary(formattedCommand);
  const language = inferCodeLanguage(formattedCommand, step.bashLanguage);
  const output = step.bashOutput ? cleanAndFormatSnippet(step.bashOutput) : "";
  const toolGroupPart = {
    type: "tool-Group",
    state: isPending ? "input-streaming" : "output-available",
    input: {},
    output: output
      ? {
          totalDurationMs: step.duration,
        }
      : undefined,
    toolCallId: step.id,
  };
  const commandSubtitle = summarizeField(formattedCommand, step.toolDetail || summary);
  const outputSubtitle = summarizeField(output);

  return (
    <div className="flex flex-col gap-1.5">
      <ToolGroup
        part={toolGroupPart}
        completeLabel="Ran command"
        shimmerLabel="Running command"
        interruptedLabel="Command interrupted"
        defaultOpen
        showElapsed={false}
      >
        <div className="flex flex-col gap-2">
          <NestedCodeRow
            label="command"
            subtitle={commandSubtitle}
            code={formattedCommand}
            filename={language === "bash" ? "command" : "repl_execute"}
            language={language}
          />
          {!isPending && output ? (
            <NestedCodeRow
              label={step.bashSuccess === false ? "stderr" : "stdout"}
              subtitle={outputSubtitle}
              code={output}
              filename={step.bashSuccess === false ? "stderr" : "stdout"}
              language={step.bashSuccess === false ? "text" : inferCodeLanguage(output, "text")}
              defaultOpen={false}
            />
          ) : null}
        </div>
      </ToolGroup>
      {approval && <ToolApprovalFooter isPending={isPending} {...approval} />}
    </div>
  );
}

export type BashToolProps = {
  part: any;
};

export const BashTool = memo(function BashTool({ part }: BashToolProps) {
  const approval = (part.input?.approval ?? part.args?.approval) as ToolApproval | undefined;
  const step = mapToolInvocationToStep(part.toolCallId ?? part.id ?? "bash", {
    toolName: "Bash",
    args: part.input ?? part.args ?? {},
    state:
      part.state === "output-available"
        ? "result"
        : part.state === "input-streaming"
          ? "partial-call"
          : "call",
    result: part.output ?? part.result,
  });
  const stepState = mapToolStateToStepState(
    part.state === "output-available"
      ? "result"
      : part.state === "input-streaming"
        ? "partial-call"
        : "call",
  );
  const noop = () => {};

  return (
    <BashToolTerminalCard step={step} state={stepState} onComplete={noop} approval={approval} />
  );
});
