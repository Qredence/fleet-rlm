import type { ReactNode } from "react";
import {
  Attachment,
  AttachmentInfo,
  Attachments,
  AttachmentPreview,
} from "@/components/ai-elements/attachments";
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation";
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
} from "@/components/ai-elements/environment-variables";
import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationQuote,
  InlineCitationSource,
  InlineCitationText,
} from "@/components/ai-elements/inline-citation";
import { Message, MessageContent } from "@/components/ai-elements/message";
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
} from "@/components/ai-elements/sandbox";
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
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
} from "@/components/ui/queue";
import { Streamdown } from "@/components/ui/streamdown";
import { TextShimmer } from "@/components/ui/text-shimmer";
import type { ChatRenderPart, ChatRenderToolState, RuntimeContext } from "@/lib/data/types";
import { cn } from "@/lib/utils/cn";
import { mapConfirmationState, mapTaskStatus, mapToolState } from "@/lib/utils/ai-elements-state";
import { RuntimeContextBadge } from "@/features/rlm-workspace/assistant-content/runtimeBadges";
import type { ToolSessionItem, TraceDisplayItem } from "@/features/rlm-workspace/chatDisplayItems";
import {
  MONO_BASE_MEDIUM_STYLE,
  MONO_BASE_STYLE,
} from "@/features/rlm-workspace/chat-shell/chatMessageStyles";

type ToolSessionDisplayItem = Extract<TraceDisplayItem, { kind: "tool_session" }>;

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function shouldOpenToolRow(state: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>["state"]) {
  return state === "running" || state === "input-streaming" || state === "output-error";
}

function shouldOpenTaskRow(status: Extract<ChatRenderPart, { kind: "task" }>["status"]) {
  return status === "in_progress" || status === "error";
}

function toolSessionStateForItem(item: ToolSessionItem): ChatRenderToolState {
  if (item.part.kind === "tool" || item.part.kind === "sandbox") {
    return item.part.state;
  }
  if (item.part.kind === "environment_variables") return "output-available";
  if (
    item.part.tone === "error" ||
    /(error|failed|failure|rejected|unable)/i.test(item.part.text)
  ) {
    return "output-error";
  }
  if (
    item.part.tone === "success" ||
    /(done|complete|completed|finished|success)/i.test(item.part.text)
  ) {
    return "output-available";
  }
  return "running";
}

function toolSessionHeaderLabel(items: ToolSessionItem[]) {
  const first = items[0];
  const toolName = first?.toolName ?? "Tool";
  return first?.eventKind === "tool_call" ? `Calling tool: ${toolName}` : `Tool: ${toolName}`;
}

function toolSessionLine(item: ToolSessionItem) {
  if (item.part.kind === "status_note") {
    return `Status: ${item.part.text}`;
  }
  const toolName = item.toolName ?? "tool";
  return `${item.eventKind}: ${toolName}`;
}

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
            <div className="space-y-3">
              {part.citations.map((citation, index) => (
                <div
                  key={`${citation.url}-${index}`}
                  className="space-y-2 rounded-md border-subtle p-2"
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
        <div className="space-y-2">
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

function renderToolSessionItemDetails(item: ToolSessionItem): ReactNode {
  if (item.part.kind === "tool") {
    const outputText = stringifyValue(item.part.output).trim();
    const hasOutput = Boolean(item.part.errorText || outputText);

    return (
      <div className="space-y-2">
        <RuntimeContextBadge ctx={item.runtimeContext} />
        {item.part.input != null ? <ToolInput input={item.part.input} /> : null}
        {hasOutput ? (
          <ToolOutput
            errorText={item.part.errorText}
            output={
              item.part.errorText ? undefined : (
                <div className="w-full">
                  <Streamdown content={outputText} streaming={false} />
                </div>
              )
            }
          />
        ) : null}
      </div>
    );
  }

  if (item.part.kind === "sandbox") {
    return (
      <div className="space-y-2">
        <RuntimeContextBadge ctx={item.runtimeContext} />
        {item.part.errorText || item.part.output ? (
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Output
            </div>
            <div
              className={cn(
                "rounded-md border px-2.5 py-2 text-foreground typo-label-regular",
                item.part.errorText
                  ? "border-destructive/25 bg-destructive/5 text-destructive"
                  : "border-border-subtle/80 bg-muted/15",
              )}
            >
              {item.part.errorText ? (
                item.part.errorText
              ) : (
                <Streamdown content={item.part.output ?? ""} streaming={false} />
              )}
            </div>
          </div>
        ) : null}
        {item.part.code ? (
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Code
            </div>
            <pre
              className="overflow-x-auto rounded-md border-subtle/80 bg-muted/20 px-2.5 py-2 text-foreground"
              style={MONO_BASE_STYLE}
            >
              <code>{item.part.code}</code>
            </pre>
          </div>
        ) : null}
      </div>
    );
  }

  if (item.part.kind === "environment_variables") {
    return (
      <div className="rounded-md border-subtle/80 bg-muted/15">
        {item.part.variables.map((variable, index) => (
          <div
            key={`${item.key}-env-${variable.name}-${index}`}
            className={cn(
              "flex flex-col gap-1 px-2.5 py-2 text-foreground",
              index > 0 && "border-t border-border-subtle/70",
            )}
          >
            <div className="flex items-center gap-2">
              <span className="text-foreground" style={MONO_BASE_MEDIUM_STYLE}>
                {variable.name}
              </span>
              {variable.required ? (
                <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  required
                </span>
              ) : null}
            </div>
            <span className="text-muted-foreground" style={MONO_BASE_STYLE}>
              {variable.value}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return <RuntimeContextBadge ctx={item.runtimeContext} />;
}

function renderReasoningPart(
  part: Extract<ChatRenderPart, { kind: "reasoning" }>,
  embedded = false,
  showSectionLabel = false,
) {
  const reasoningText = part.parts.map((item) => item.text).join("\n");
  const sectionLabel = part.label?.trim() || "reasoning";

  return (
    <div className="space-y-1">
      {showSectionLabel ? (
        <div
          className="text-[11px] lowercase tracking-[0.08em] text-muted-foreground"
          style={MONO_BASE_MEDIUM_STYLE}
        >
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

function renderCompactStatusAlert(
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
        <div className="space-y-1">
          <div className="typo-label-regular">{content}</div>
          <RuntimeContextBadge ctx={runtimeContext} />
        </div>
      </AlertDescription>
    </Alert>
  );
}

export function ChatMessageLoadingState() {
  return (
    <div>
      <TextShimmer as="span" className="text-sm text-muted-foreground">
        Generating code...
      </TextShimmer>
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
    case "chain_of_thought":
      return (
        <ChainOfThought defaultOpen={false}>
          <ChainOfThoughtHeader>{part.title ?? "Execution trace"}</ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            <div className="divide-y divide-border-subtle">
              {part.steps.map((step) => (
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
              ))}
            </div>
          </ChainOfThoughtContent>
        </ChainOfThought>
      );
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
              <div className="space-y-1">
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
      const outputText = stringifyValue(part.output);
      return (
        <Tool defaultOpen={shouldOpenToolRow(part.state)}>
          <ToolHeader
            type={`tool-${part.toolType}`}
            state={mapToolState(part.state)}
            title={part.title || part.toolType}
          />
          <ToolContent>
            <RuntimeContextBadge ctx={part.runtimeContext} />
            {part.input != null ? <ToolInput input={part.input} /> : null}
            <ToolOutput
              errorText={part.errorText}
              output={
                part.errorText ? undefined : outputText ? (
                  <div className="w-full">
                    <Streamdown content={outputText} streaming={false} />
                  </div>
                ) : (
                  <span className="text-muted-foreground">No output</span>
                )
              }
            />
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
                  <pre
                    className="overflow-x-auto rounded-md border-subtle bg-muted/30 p-2"
                    style={MONO_BASE_STYLE}
                  >
                    <code>{code}</code>
                  </pre>
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
                  <div className="space-y-1">
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
              {part.actions?.map((action) => (
                <ConfirmationAction
                  key={action.label}
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
            <div className="mt-2 text-xs text-emerald-600">Approved</div>
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

interface WorkspaceToolSessionMessageProps {
  item: ToolSessionDisplayItem;
}

export function WorkspaceToolSessionMessage({ item }: WorkspaceToolSessionMessageProps) {
  const fallbackState: ChatRenderToolState = "running";
  const latestItem = item.items[item.items.length - 1];
  const latestState = latestItem ? toolSessionStateForItem(latestItem) : fallbackState;

  return (
    <Message from="assistant" className="mb-4">
      <MessageContent className="w-full">
        <Tool defaultOpen={shouldOpenToolRow(latestState)}>
          <ToolHeader
            type="tool-default"
            state={mapToolState(latestState)}
            title={toolSessionHeaderLabel(item.items)}
          />
          <ToolContent className="space-y-3">
            {item.items.map((sessionItem) => (
              <div
                key={sessionItem.key}
                className="border-l border-border-subtle/70 pl-3"
                data-slot="tool-session-item"
              >
                <div className="space-y-2 py-0.5">
                  <div className="text-foreground typo-label-regular">
                    {toolSessionLine(sessionItem)}
                  </div>
                  {renderToolSessionItemDetails(sessionItem)}
                </div>
              </div>
            ))}
          </ToolContent>
        </Tool>
      </MessageContent>
    </Message>
  );
}

interface WorkspaceLegacyStatusCardProps {
  content: string;
  tone: "accent" | "primary" | "success";
}

export function WorkspaceLegacyStatusCard({ content, tone }: WorkspaceLegacyStatusCardProps) {
  return renderCompactStatusAlert(content, tone);
}
