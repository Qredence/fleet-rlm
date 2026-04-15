import type { ReactNode } from "react";
import { useState } from "react";
import {
  Attachment,
  AttachmentInfo,
  Attachments,
  AttachmentPreview,
} from "@/features/workspace/ui/attachments";
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/features/workspace/ui/chain-of-thought";
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/features/workspace/ui/confirmation";
import {
  EnvironmentVariable,
  EnvironmentVariableCopyButton,
  EnvironmentVariableGroup,
  EnvironmentVariableName,
  EnvironmentVariableRequired,
  EnvironmentVariables,
  EnvironmentVariablesContent,
  EnvironmentVariablesHeader,
  EnvironmentVariablesTitle,
  EnvironmentVariablesToggle,
  EnvironmentVariableValue,
} from "@/features/workspace/ui/environment-variables";
import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationQuote,
  InlineCitationSource,
  InlineCitationText,
} from "@/components/ai-elements/inline-citation";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import {
  Sandbox,
  SandboxContent,
  SandboxHeader,
  SandboxTabContent,
  SandboxTabs,
  SandboxTabsBar,
  SandboxTabsList,
  SandboxTabsTrigger,
} from "@/features/workspace/ui/sandbox";
import { Source, Sources, SourcesContent, SourcesTrigger } from "@/components/ai-elements/sources";
import {
  Task,
  TaskContent,
  TaskItem,
  TaskItemFile,
  TaskTrigger,
} from "@/components/ai-elements/task";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { CodeBlock, CodeBlockCode } from "@/components/ui/code-block";
import {
  Queue,
  QueueItem,
  QueueItemContent,
  QueueItemDescription,
  QueueItemIndicator,
  QueueList,
  QueueSection,
  QueueSectionContent,
  QueueSectionLabel,
  QueueSectionTrigger,
} from "@/features/workspace/ui/queue";
import { Streamdown } from "@/components/ui/streamdown";
import { TextShimmer } from "@/components/effects/text-shimmer";
import type { ChatRenderPart, RuntimeContext } from "@/features/workspace/use-workspace";
import { cn } from "@/lib/utils";
import { mapConfirmationState, mapTaskStatus, mapToolState } from "@/lib/utils/prompt-kit-state";
import { RuntimeContextBadge } from "@/features/workspace/ui/assistant-content/model";
import { stringifyValue, shouldOpenToolRow, shouldOpenTaskRow } from "./trace-part-tool";

function renderInlineCitations(part: Extract<ChatRenderPart, { kind: "inline_citation_group" }>) {
  return (
    <div className="mt-2">
      <InlineCitation>
        <InlineCitationText>
          <span className="text-xs text-muted-foreground">Sources</span>
        </InlineCitationText>
        <InlineCitationCard>
          <InlineCitationCardTrigger sources={part.citations.map((citation) => citation.url)} />
          <InlineCitationCardBody>
            <div className="flex flex-col gap-3">
              {part.citations.map((citation, index) => (
                <div
                  key={`${citation.url}-${index}`}
                  className="flex flex-col gap-2 rounded-md border-subtle p-2"
                >
                  <InlineCitationSource
                    title={citation.title}
                    url={citation.url}
                    description={citation.description}
                  />
                  {citation.quote ? (
                    <InlineCitationQuote>{citation.quote}</InlineCitationQuote>
                  ) : null}
                </div>
              ))}
            </div>
          </InlineCitationCardBody>
        </InlineCitationCard>
      </InlineCitation>
    </div>
  );
}

function renderSources(part: Extract<ChatRenderPart, { kind: "sources" }>): ReactNode {
  if (part.sources.length === 0) return null;
  return (
    <Sources defaultOpen={false}>
      <SourcesTrigger count={part.sources.length} />
      <SourcesContent>
        <div className="flex flex-col gap-2">
          {part.sources.map((source) => (
            <Source
              key={`${source.sourceId}-${source.url ?? source.canonicalUrl ?? source.title}`}
              href={source.url ?? source.canonicalUrl ?? "#"}
              title={source.title ?? "Source"}
            >
              {source.description || source.quote || source.displayUrl}
            </Source>
          ))}
        </div>
      </SourcesContent>
    </Sources>
  );
}

function renderAttachments(part: Extract<ChatRenderPart, { kind: "attachments" }>): ReactNode {
  if (part.attachments.length === 0) return null;
  return (
    <Attachments variant={part.variant ?? "grid"}>
      {part.attachments.map((attachment) => (
        <Attachment
          key={attachment.attachmentId}
          data={{
            id: attachment.attachmentId,
            type: "file",
            filename: attachment.name ?? "unknown",
            url: attachment.url ?? "",
            mediaType: attachment.mimeType ?? attachment.mediaType ?? "application/octet-stream",
          }}
        >
          <AttachmentPreview />
          <AttachmentInfo showMediaType />
        </Attachment>
      ))}
    </Attachments>
  );
}

function renderReasoningPart(
  part: Extract<ChatRenderPart, { kind: "reasoning" }>,
  embedded = false,
  showSectionLabel = false,
) {
  const reasoningText = part.parts.map((item) => item.text).join("\n");
  const sectionLabel = part.label?.trim() || "reasoning";

  return (
    <div className="flex flex-col gap-1">
      {showSectionLabel ? (
        <div className="font-mono text-[11px] font-medium lowercase tracking-[0.08em] text-muted-foreground">
          {sectionLabel}
        </div>
      ) : null}
      <Reasoning
        isStreaming={part.isStreaming}
        duration={part.duration}
        className={cn(
          "w-full",
          embedded && "rounded-none border-0 bg-transparent px-0 py-0 shadow-none",
        )}
      >
        <ReasoningTrigger />
        <ReasoningContent>{reasoningText}</ReasoningContent>
      </Reasoning>
      <RuntimeContextBadge ctx={part.runtimeContext} />
    </div>
  );
}

function compactStatusClasses(
  tone:
    | Extract<ChatRenderPart, { kind: "status_note" }>["tone"]
    | "accent"
    | "primary"
    | "success"
    | undefined,
) {
  if (tone === "error") return undefined;
  return cn(
    "px-3 py-2.5",
    tone === "warning" && "border-accent/25 bg-accent/5 text-foreground",
    tone === "accent" && "border-accent/25 bg-accent/5 text-foreground",
    tone === "primary" && "border-primary/25 bg-primary/5 text-foreground",
    tone === "success" && "border-primary/25 bg-primary/5 text-foreground",
    (!tone || tone === "neutral") && "border-border-subtle/80 bg-muted/20 text-muted-foreground",
  );
}

export function renderCompactStatusAlert(
  content: string,
  tone:
    | Extract<ChatRenderPart, { kind: "status_note" }>["tone"]
    | "accent"
    | "primary"
    | "success"
    | undefined,
  runtimeContext?: RuntimeContext,
) {
  return (
    <Alert
      variant={tone === "error" ? "destructive" : "default"}
      className={compactStatusClasses(tone)}
    >
      <AlertDescription>
        <div className="flex flex-col gap-1">
          <div className="typo-label-regular">{content}</div>
          <RuntimeContextBadge ctx={runtimeContext} />
        </div>
      </AlertDescription>
    </Alert>
  );
}

export function ChatMessageLoadingState() {
  return (
    <div className="flex items-center gap-2.5 py-1">
      <div className="flex gap-1">
        <span className="size-1.5 rounded-full bg-primary/60 animate-bounce [animation-delay:-0.3s]" />
        <span className="size-1.5 rounded-full bg-primary/60 animate-bounce [animation-delay:-0.15s]" />
        <span className="size-1.5 rounded-full bg-primary/60 animate-bounce" />
      </div>
      <TextShimmer as="span" className="text-sm text-muted-foreground">
        Setting up your workspace…
      </TextShimmer>
    </div>
  );
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function ToolIOPreview({
  label,
  value,
}: {
  label: string;
  value: unknown;
}): ReactNode {
  const [expanded, setExpanded] = useState(false);

  const fullText = stringifyValue(value);
  const preview = fullText.length > 80 ? `${fullText.slice(0, 80)}…` : fullText;

  if (!fullText) return null;

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        className="flex items-center gap-1 text-left"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </span>
        <span className="ml-1 text-[10px] text-muted-foreground/60">
          {expanded ? "▲" : "▼"}
        </span>
      </button>
      {expanded ? (
        <div className="w-full">
          <Streamdown content={fullText} streaming={false} />
        </div>
      ) : (
        <div className="truncate font-mono text-xs text-muted-foreground">{preview}</div>
      )}
    </div>
  );
}

interface BatchGroupProps {
  groupId: string;
  steps: Extract<ChatRenderPart, { kind: "chain_of_thought" }>["steps"];
  mapStatus: (s: "pending" | "in_progress" | "completed" | "error") => ReturnType<typeof mapTaskStatus>;
}

function BatchGroup({ groupId, steps, mapStatus }: BatchGroupProps): ReactNode {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border-l-2 border-border-subtle/50 pl-2">
      <button
        type="button"
        className="flex items-center gap-1 py-0.5 text-left text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span>batch ({steps.length})</span>
        <span className="text-[10px]">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded ? (
        <div className="divide-y divide-border-subtle">
          {steps.map((step) => (
            <ChainOfThoughtStep
              key={`${groupId}-${step.id}`}
              label={step.label}
              status={mapStatus(
                step.status as "pending" | "in_progress" | "completed" | "error",
              )}
            >
              {step.details?.map((detail, index) => (
                <div key={`${step.id}-detail-${index}`}>{detail}</div>
              ))}
            </ChainOfThoughtStep>
          ))}
        </div>
      ) : null}
    </div>
  );
}

interface WorkspaceTracePartProps {
  part: ChatRenderPart;
  partKey: string;
}

export function WorkspaceTracePart({ part, partKey }: WorkspaceTracePartProps) {
  switch (part.kind) {
    case "reasoning":
      return renderReasoningPart(part);
    case "chain_of_thought": {
      // Group steps by batchGroupId: consecutive steps with the same groupId
      // are collapsed under a "batch (N)" header.
      type StepNode =
        | { kind: "step"; step: (typeof part.steps)[number] }
        | {
            kind: "batch";
            groupId: string;
            steps: (typeof part.steps)[number][];
          };

      const nodes: StepNode[] = [];
      for (const step of part.steps) {
        const batchGroupId = step.batchGroupId;
        if (batchGroupId) {
          const last = nodes[nodes.length - 1];
          if (last?.kind === "batch" && last.groupId === batchGroupId) {
            last.steps.push(step);
          } else {
            nodes.push({ kind: "batch", groupId: batchGroupId, steps: [step] });
          }
        } else {
          nodes.push({ kind: "step", step });
        }
      }

      return (
        <ChainOfThought defaultOpen={false}>
          <ChainOfThoughtHeader>{part.title ?? "Execution trace"}</ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            <div className="divide-y divide-border-subtle">
              {nodes.map((node, nodeIndex) => {
                if (node.kind === "batch") {
                  return (
                    <BatchGroup
                      key={`batch-${node.groupId}-${nodeIndex}`}
                      groupId={node.groupId}
                      steps={node.steps}
                      mapStatus={mapTaskStatus}
                    />
                  );
                }
                const { step } = node;
                return (
                  <ChainOfThoughtStep
                    key={step.id}
                    label={step.label}
                    status={mapTaskStatus(
                      step.status as "pending" | "in_progress" | "completed" | "error",
                    )}
                  >
                    {step.details?.map((detail, index) => (
                      <div key={`${step.id}-detail-${index}`}>{detail}</div>
                    ))}
                  </ChainOfThoughtStep>
                );
              })}
            </div>
          </ChainOfThoughtContent>
        </ChainOfThought>
      );
    }
    case "queue":
      return (
        <Queue>
          <QueueSection defaultOpen>
            <QueueSectionTrigger>
              <QueueSectionLabel label={part.title} count={part.items.length} />
            </QueueSectionTrigger>
            <QueueSectionContent>
              <QueueList>
                {part.items.map((item) => (
                  <QueueItem key={item.id}>
                    <QueueItemIndicator completed={item.completed} />
                    <QueueItemContent completed={item.completed}>{item.label}</QueueItemContent>
                    {item.description ? (
                      <QueueItemDescription completed={item.completed}>
                        {item.description}
                      </QueueItemDescription>
                    ) : null}
                  </QueueItem>
                ))}
              </QueueList>
            </QueueSectionContent>
          </QueueSection>
        </Queue>
      );
    case "task":
      return (
        <Task defaultOpen={shouldOpenTaskRow(part.status)}>
          <TaskTrigger title={part.title} />
          <TaskContent>
            {part.items?.length ? (
              <div className="flex flex-col gap-1">
                {part.items.map((item) => (
                  <TaskItem key={item.id}>
                    <span>{item.text}</span>
                    {item.file ? (
                      <TaskItemFile className="ml-2">{item.file.name}</TaskItemFile>
                    ) : null}
                  </TaskItem>
                ))}
              </div>
            ) : (
              <TaskItem>No additional details</TaskItem>
            )}
          </TaskContent>
        </Task>
      );
    case "tool": {
      const toolTitle = part.title || part.toolType;
      const displayTitle =
        part.latencyMs != null ? `${toolTitle} · ${formatLatency(part.latencyMs)}` : toolTitle;
      return (
        <Tool defaultOpen={shouldOpenToolRow(part.state)}>
          <ToolHeader
            type={`tool-${part.toolType}`}
            state={mapToolState(part.state)}
            title={displayTitle}
          />
          <ToolContent>
            <RuntimeContextBadge ctx={part.runtimeContext} />
            {part.input != null ? (
              <ToolIOPreview label="Input" value={part.input} />
            ) : null}
            {part.errorText ? (
              <ToolOutput errorText={part.errorText} output={undefined} />
            ) : part.output != null ? (
              <ToolIOPreview label="Output" value={part.output} />
            ) : null}
          </ToolContent>
        </Tool>
      );
    }
    case "sandbox": {
      const code = part.code ?? "";
      const output = part.output ?? "";
      return (
        <Sandbox defaultOpen={shouldOpenToolRow(part.state)}>
          <SandboxHeader title={part.title} state={mapToolState(part.state)} />
          <SandboxContent>
            <div className="px-2.5 py-1.5">
              <RuntimeContextBadge ctx={part.runtimeContext} />
            </div>
            <SandboxTabs defaultValue="output">
              <SandboxTabsBar>
                <SandboxTabsList>
                  <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
                  <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                </SandboxTabsList>
              </SandboxTabsBar>
              <SandboxTabContent value="output">
                {part.errorText ? (
                  <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive typo-label-regular">
                    {part.errorText}
                  </div>
                ) : output ? (
                  <Streamdown content={output} streaming={false} />
                ) : (
                  <div className="text-muted-foreground typo-label-regular">No output yet</div>
                )}
              </SandboxTabContent>
              <SandboxTabContent value="code">
                {code ? (
                  <CodeBlock className="border-subtle bg-muted/30">
                    <CodeBlockCode code={code} language="python" />
                  </CodeBlock>
                ) : (
                  <div className="text-muted-foreground typo-label-regular">No code captured</div>
                )}
              </SandboxTabContent>
            </SandboxTabs>
          </SandboxContent>
        </Sandbox>
      );
    }
    case "environment_variables":
      return (
        <EnvironmentVariables defaultShowValues={false}>
          <EnvironmentVariablesHeader>
            <EnvironmentVariablesTitle>
              {part.title ?? "Environment variables"}
            </EnvironmentVariablesTitle>
            <EnvironmentVariablesToggle />
          </EnvironmentVariablesHeader>
          <EnvironmentVariablesContent>
            {part.variables.map((variable, index) => (
              <EnvironmentVariable
                key={`${variable.name}-${index}`}
                name={variable.name}
                value={variable.value}
              >
                <EnvironmentVariableGroup>
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <EnvironmentVariableName />
                      {variable.required ? <EnvironmentVariableRequired /> : null}
                    </div>
                    <EnvironmentVariableValue />
                  </div>
                  <EnvironmentVariableCopyButton aria-label={`Copy ${variable.name}`} />
                </EnvironmentVariableGroup>
              </EnvironmentVariable>
            ))}
          </EnvironmentVariablesContent>
        </EnvironmentVariables>
      );
    case "inline_citation_group":
      return <div>{renderInlineCitations(part)}</div>;
    case "sources":
      return <div>{renderSources(part)}</div>;
    case "attachments":
      return <div>{renderAttachments(part)}</div>;
    case "status_note":
      return <div>{renderCompactStatusAlert(part.text, part.tone, part.runtimeContext)}</div>;
    case "confirmation":
      return (
        <Confirmation
          state={mapConfirmationState(part.state)}
          approval={{
            id: partKey,
            approved:
              part.state === "approved" ? true : part.state === "rejected" ? false : undefined,
          }}
        >
          <ConfirmationTitle>{part.question}</ConfirmationTitle>
          <ConfirmationRequest>
            <ConfirmationActions>
              {part.actions?.map((action, index) => (
                <ConfirmationAction
                  key={`${partKey}-action-${index}`}
                  className={cn(
                    action.variant === "primary" &&
                      "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
                  )}
                >
                  {action.label}
                </ConfirmationAction>
              ))}
            </ConfirmationActions>
          </ConfirmationRequest>
          <ConfirmationAccepted>
            <div className="mt-2 text-xs text-success">Approved</div>
          </ConfirmationAccepted>
          <ConfirmationRejected>
            <div className="mt-2 text-xs text-destructive">Rejected</div>
          </ConfirmationRejected>
        </Confirmation>
      );
    default:
      return null;
  }
}

interface WorkspaceLegacyStatusCardProps {
  content: string;
  tone: "accent" | "primary" | "success";
}

export function WorkspaceLegacyStatusCard({ content, tone }: WorkspaceLegacyStatusCardProps) {
  return renderCompactStatusAlert(content, tone);
}
