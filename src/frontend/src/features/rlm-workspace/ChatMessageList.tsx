import { type RefObject, type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { Clock } from "lucide-react";
import type {
  ChatMessage,
  ChatRenderPart,
  ChatRenderToolState,
  RuntimeContext,
} from "@/lib/data/types";
import { typo } from "@/lib/config/typo";
import { cn } from "@/lib/utils/cn";
import { mapToolState, mapConfirmationState, mapTaskStatus } from "@/lib/utils/ai-elements-state";
import { Streamdown } from "@/components/ui/streamdown";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import { Reasoning, ReasoningTrigger, ReasoningContent } from "@/components/ai-elements/reasoning";
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
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
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
import {
  Attachments,
  Attachment,
  AttachmentInfo,
  AttachmentPreview,
} from "@/components/ai-elements/attachments";
import {
  Source,
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources";
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { ClarificationCard } from "@/features/rlm-workspace/ClarificationCard";
import { SuggestionChip } from "@/components/ui/suggestion-chip";
import {
  SuggestionIconBolt,
  SuggestionIconTune,
  SuggestionIconSparkle,
} from "@/components/shared/SuggestionIcons";
import {
  fadeUp,
  fadeUpReduced,
} from "@/features/rlm-workspace/animation-presets";
import {
  buildChatDisplayItems,
  type AssistantTurnDisplayItem,
  type ToolSessionItem,
  type TraceDisplayItem,
} from "@/features/rlm-workspace/chatDisplayItems";

const suggestions = [
  {
    text: "Analyze a codebase and extract its architecture",
    Icon: SuggestionIconBolt,
  },
  {
    text: "Summarize this document and find key insights",
    Icon: SuggestionIconTune,
  },
  {
    text: "Write and execute a Python script for me",
    Icon: SuggestionIconSparkle,
  },
];

interface ChatMessageListProps {
  messages: ChatMessage[];
  isTyping: boolean;
  isMobile: boolean;
  scrollRef: RefObject<HTMLDivElement | null>;
  contentRef: RefObject<HTMLDivElement | null>;
  isAtBottom?: boolean;
  scrollToBottom?: () => void;
  onSuggestionClick: (text: string) => void;
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
  showHistory?: boolean;
  onToggleHistory?: () => void;
  hasHistory?: boolean;
  historyPanel?: ReactNode;
}

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

function hitlConfirmationState(
  msg: Extract<
    ChatMessage,
    { hitlData?: NonNullable<ChatMessage["hitlData"]> }
  >,
): "approval-requested" | "approved" | "rejected" {
  const data = msg.hitlData;
  if (!data?.resolved) return "approval-requested";
  const label = String(data.resolvedLabel ?? "").toLowerCase();
  if (/(reject|deny|decline|cancel|no)/.test(label)) return "rejected";
  return "approved";
}

function RuntimeContextBadge({ ctx }: { ctx?: RuntimeContext }) {
  if (!ctx) return null;
  const pills: string[] = [];
  if (ctx.depth > 0) pills.push(`depth ${ctx.depth}/${ctx.maxDepth}`);
  if (ctx.executionMode && ctx.executionMode !== "react") {
    pills.push(`mode ${ctx.executionMode}`);
  }
  if (ctx.sandboxActive) pills.push("sandbox");
  if (ctx.sandboxId) pills.push(`sandbox ${ctx.sandboxId.slice(0, 10)}`);
  if (ctx.volumeName) pills.push(ctx.volumeName);
  if (ctx.executionProfile !== "ROOT_INTERLOCUTOR")
    pills.push(ctx.executionProfile.toLowerCase().replace(/_/g, " "));
  if (pills.length === 0) return null;
  return (
    <div className="text-[10px] leading-relaxed text-muted-foreground">
      {pills.join(" · ")}
    </div>
  );
}

function shouldOpenToolRow(
  state: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>["state"],
) {
  return (
    state === "running" ||
    state === "input-streaming" ||
    state === "output-error"
  );
}

function shouldOpenTaskRow(
  status: Extract<ChatRenderPart, { kind: "task" }>["status"],
) {
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
  return first?.eventKind === "tool_call"
    ? `Calling tool: ${toolName}`
    : `Tool: ${toolName}`;
}

function toolSessionLine(item: ToolSessionItem) {
  if (item.part.kind === "status_note") {
    return `Status: ${item.part.text}`;
  }
  const toolName = item.toolName ?? "tool";
  return `${item.eventKind}: ${toolName}`;
}

function renderInlineCitations(
  part: Extract<ChatRenderPart, { kind: "inline_citation_group" }>,
) {
  return (
    <div className="mt-2">
      <InlineCitation>
        <InlineCitationText>
          <span className="text-xs text-muted-foreground">Sources</span>
        </InlineCitationText>
        <InlineCitationCard>
          <InlineCitationCardTrigger
            sources={part.citations.map((c) => c.title)}
          />
          <InlineCitationCardBody>
            <div className="space-y-3">
              {part.citations.map((citation, idx) => (
                <div
                  key={`${citation.url}-${idx}`}
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

function renderSources(
  part: Extract<ChatRenderPart, { kind: "sources" }>,
): ReactNode {
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

function renderAttachments(
  part: Extract<ChatRenderPart, { kind: "attachments" }>,
): ReactNode {
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
                "rounded-md border px-2.5 py-2 text-foreground",
                item.part.errorText
                  ? "border-destructive/25 bg-destructive/5 text-destructive"
                  : "border-border-subtle/80 bg-muted/15",
              )}
              style={typo.base}
            >
              {item.part.errorText ? (
                item.part.errorText
              ) : (
                <Streamdown
                  content={item.part.output ?? ""}
                  streaming={false}
                />
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
              style={{ ...typo.base, fontFamily: "var(--font-family-mono)" }}
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
        {item.part.variables.map((variable, idx) => (
          <div
            key={`${item.key}-env-${variable.name}-${idx}`}
            className={cn(
              "flex flex-col gap-1 px-2.5 py-2 text-foreground",
              idx > 0 && "border-t border-border-subtle/70",
            )}
          >
            <div className="flex items-center gap-2">
              <span
                className="text-foreground"
                style={{
                  ...typo.base,
                  fontFamily: "var(--font-family-mono)",
                  fontWeight: "var(--font-weight-medium)",
                }}
              >
                {variable.name}
              </span>
              {variable.required ? (
                <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  required
                </span>
              ) : null}
            </div>
            <span
              className="text-muted-foreground"
              style={{
                ...typo.base,
                fontFamily: "var(--font-family-mono)",
              }}
            >
              {variable.value}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return <RuntimeContextBadge ctx={item.runtimeContext} />;
}

function renderToolSession(
  item: Extract<TraceDisplayItem, { kind: "tool_session" }>,
) {
  const fallbackState: ChatRenderToolState = "running";
  const latestItem = item.items[item.items.length - 1];
  const latestState = latestItem
    ? toolSessionStateForItem(latestItem)
    : fallbackState;

  return (
    <Message from="assistant" className="mb-4" key={item.key}>
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
                  <div className="text-foreground" style={typo.base}>
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

function renderReasoningPart(
  part: Extract<ChatRenderPart, { kind: "reasoning" }>,
  key: string,
  embedded = false,
) {
  // Combine parts into a single text string for ReasoningContent
  const reasoningText = part.parts.map(p => p.text).join("\n");

  return (
    <div key={key} className="space-y-1">
      <Reasoning
        isStreaming={part.isStreaming}
        duration={part.duration}
        className={cn(
          "w-full",
          embedded &&
            "rounded-none border-0 bg-transparent px-0 py-0 shadow-none",
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
    (!tone || tone === "neutral") &&
      "border-border-subtle/80 bg-muted/20 text-muted-foreground",
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
          <div style={typo.base}>{content}</div>
          <RuntimeContextBadge ctx={runtimeContext} />
        </div>
      </AlertDescription>
    </Alert>
  );
}

function buildAssistantTurnReasoningParts(item: AssistantTurnDisplayItem) {
  const fromTrace = item.reasoningItems.map((reasoningItem) => ({
    key: reasoningItem.key,
    part: reasoningItem.part,
  }));
  const message = item.message;
  const fromMessage =
    message?.renderParts?.flatMap((part, idx) =>
      part.kind === "reasoning"
        ? [{ key: `${message.id}-${part.kind}-${idx}`, part }]
        : [],
    ) ?? [];
  return [...fromTrace, ...fromMessage];
}

function mergeReasoningParts(
  reasoningParts: ReturnType<typeof buildAssistantTurnReasoningParts>,
) {
  if (reasoningParts.length === 0) return [];

  const mergedText = reasoningParts
    .flatMap(({ part }) => part.parts)
    .map((part) => part.text)
    .join("");
  const lastPart = reasoningParts[reasoningParts.length - 1]?.part;
  const runtimeContext = [...reasoningParts]
    .reverse()
    .find(({ part }) => part.runtimeContext)?.part.runtimeContext;
  const duration = [...reasoningParts]
    .reverse()
    .find(({ part }) => part.duration != null)?.part.duration;

  if (!lastPart) return [];

  return [
    {
      key: reasoningParts[0]?.key ?? "reasoning",
      part: {
        kind: "reasoning" as const,
        parts: [{ type: "text" as const, text: mergedText }],
        isStreaming: lastPart.isStreaming,
        ...(duration != null ? { duration } : {}),
        ...(runtimeContext ? { runtimeContext } : {}),
      },
    },
  ];
}

function buildAssistantTurnSupplementalParts(item: AssistantTurnDisplayItem) {
  return (
    item.message?.renderParts?.filter((part) => part.kind !== "reasoning") ?? []
  );
}

function renderAssistantTurn(item: AssistantTurnDisplayItem) {
  const reasoningParts = mergeReasoningParts(
    buildAssistantTurnReasoningParts(item),
  );
  const supplementalParts = buildAssistantTurnSupplementalParts(item);
  const assistantContent = item.message?.content ?? "";
  const hasReasoning = reasoningParts.length > 0;
  const hasAssistantContent = assistantContent.length > 0;
  const showStreamingShell =
    Boolean(item.message?.streaming) && !hasAssistantContent && !hasReasoning;
  const shouldRenderBubble =
    hasReasoning || hasAssistantContent || showStreamingShell;

  return (
    <Message from="assistant" className="mb-4" key={item.key}>
      <MessageContent className="w-full space-y-2.5">
        {shouldRenderBubble ? (
          <div className="max-w-content rounded-[22px] border-subtle/80 px-4 py-3.5 shadow-sm md:px-5 md:py-4">
            <div className="flex flex-col gap-3">
              {reasoningParts.map(({ key, part }) =>
                renderReasoningPart(part, key, true),
              )}
              {hasReasoning && hasAssistantContent ? (
                <Separator className="bg-border-subtle/70" />
              ) : null}
              {hasAssistantContent ? (
                <MessageResponse>
                  {assistantContent}
                </MessageResponse>
              ) : null}
              {showStreamingShell ? (
                <Shimmer as="span" className="text-sm text-muted-foreground">
                  Loading
                </Shimmer>
              ) : null}
            </div>
          </div>
        ) : null}

        {supplementalParts.map((part, idx) =>
          renderTracePart(part, `${item.key}-${part.kind}-${idx}`),
        )}
      </MessageContent>
    </Message>
  );
}

function renderTracePart(part: ChatRenderPart, key: string) {
  switch (part.kind) {
    case "reasoning":
      return renderReasoningPart(part, key);
    case "chain_of_thought":
      return (
        <ChainOfThought key={key} defaultOpen={false}>
          <ChainOfThoughtHeader>
            {part.title ?? "Execution trace"}
          </ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            <div className="divide-y divide-border-subtle">
              {part.steps.map((step) => (
                <ChainOfThoughtStep
                  key={step.id}
                  label={step.label}
                  status={mapTaskStatus(step.status as "pending" | "in_progress" | "completed" | "error")}
                >
                  {step.details?.map((detail, idx) => (
                    <div key={`${step.id}-detail-${idx}`}>{detail}</div>
                  ))}
                </ChainOfThoughtStep>
              ))}
            </div>
          </ChainOfThoughtContent>
        </ChainOfThought>
      );
    case "queue":
      return (
        <Queue key={key}>
          <QueueSection defaultOpen>
            <QueueSectionTrigger>
              <QueueSectionLabel label={part.title} count={part.items.length} />
            </QueueSectionTrigger>
            <QueueSectionContent>
              <QueueList>
                {part.items.map((item) => (
                  <QueueItem key={item.id}>
                    <QueueItemIndicator completed={item.completed} />
                    <QueueItemContent completed={item.completed}>
                      {item.label}
                    </QueueItemContent>
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
        <Task
          key={key}
          defaultOpen={shouldOpenTaskRow(part.status)}
        >
          <TaskTrigger title={part.title} />
          <TaskContent>
            {part.items?.length ? (
              <div className="space-y-1">
                {part.items.map((item) => (
                  <TaskItem key={item.id}>
                    <span>{item.text}</span>
                    {item.file ? (
                      <TaskItemFile className="ml-2">
                        {item.file.name}
                      </TaskItemFile>
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
        <Tool
          key={key}
          defaultOpen={shouldOpenToolRow(part.state)}
        >
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
        <Sandbox
          key={key}
          defaultOpen={shouldOpenToolRow(part.state)}
        >
          <SandboxHeader
            title={part.title}
            state={mapToolState(part.state)}
          />
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
                  <div
                    className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive"
                    style={typo.base}
                  >
                    {part.errorText}
                  </div>
                ) : output ? (
                  <Streamdown content={output} streaming={false} />
                ) : (
                  <div className="text-muted-foreground" style={typo.base}>
                    No output yet
                  </div>
                )}
              </SandboxTabContent>
              <SandboxTabContent value="code">
                {code ? (
                  <pre
                    className="overflow-x-auto rounded-md border-subtle bg-muted/30 p-2"
                    style={{
                      ...typo.base,
                      fontFamily: "var(--font-family-mono)",
                    }}
                  >
                    <code>{code}</code>
                  </pre>
                ) : (
                  <div className="text-muted-foreground" style={typo.base}>
                    No code captured
                  </div>
                )}
              </SandboxTabContent>
            </SandboxTabs>
          </SandboxContent>
        </Sandbox>
      );
    }
    case "environment_variables":
      return (
        <EnvironmentVariables key={key} defaultShowValues={false}>
          <EnvironmentVariablesHeader>
            <EnvironmentVariablesTitle>
              {part.title ?? "Environment variables"}
            </EnvironmentVariablesTitle>
            <EnvironmentVariablesToggle />
          </EnvironmentVariablesHeader>
          <EnvironmentVariablesContent>
            {part.variables.map((variable, idx) => (
              <EnvironmentVariable
                key={`${variable.name}-${idx}`}
                name={variable.name}
                value={variable.value}
              >
                <EnvironmentVariableGroup>
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <EnvironmentVariableName />
                      {variable.required ? (
                        <EnvironmentVariableRequired />
                      ) : null}
                    </div>
                    <EnvironmentVariableValue />
                  </div>
                  <EnvironmentVariableCopyButton
                    aria-label={`Copy ${variable.name}`}
                  />
                </EnvironmentVariableGroup>
              </EnvironmentVariable>
            ))}
          </EnvironmentVariablesContent>
        </EnvironmentVariables>
      );
    case "inline_citation_group":
      return <div key={key}>{renderInlineCitations(part)}</div>;
    case "sources":
      return <div key={key}>{renderSources(part)}</div>;
    case "attachments":
      return <div key={key}>{renderAttachments(part)}</div>;
    case "status_note":
      return (
        <div key={key}>
          {renderCompactStatusAlert(part.text, part.tone, part.runtimeContext)}
        </div>
      );
    case "confirmation":
      return (
        <Confirmation
          key={key}
          state={mapConfirmationState(part.state)}
          approval={{
            id: key,
            approved: part.state === "approved" ? true : part.state === "rejected" ? false : undefined,
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

function renderLegacyStatusCard(
  content: string,
  tone: "accent" | "primary" | "success",
) {
  return renderCompactStatusAlert(content, tone);
}

export function ChatMessageList({
  messages,
  isTyping,
  isMobile,
  scrollRef: _scrollRef,
  contentRef: _contentRef,
  isAtBottom: _isAtBottom,
  scrollToBottom: _scrollToBottom,
  onSuggestionClick,
  onResolveHitl,
  onResolveClarification,
  showHistory,
  onToggleHistory,
  hasHistory,
  historyPanel,
}: ChatMessageListProps) {
  const prefersReduced = useReducedMotion();
  const preset = prefersReduced ? fadeUpReduced : fadeUp;
  const hasStreamingAssistant = messages.some(
    (msg) => msg.type === "assistant" && msg.streaming,
  );
  let activeTurnStartIndex = 0;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.type === "user") {
      activeTurnStartIndex = index + 1;
      break;
    }
  }
  const displayItems = buildChatDisplayItems(messages);
  const hasVisibleAssistantTurn = buildChatDisplayItems(
    messages.slice(activeTurnStartIndex),
  ).some(
    (item) => item.kind === "assistant_turn",
  );
  const showTypingShimmer =
    isTyping && !hasStreamingAssistant && !hasVisibleAssistantTurn;

  return (
    <Conversation className="bg-background">
      <ConversationContent>
        {messages.length === 0 && (
          <motion.div {...preset}>
            <ConversationEmptyState
              icon={null}
              className={cn(
                "pt-16 pb-8 text-left items-start",
                isMobile && "pt-10",
              )}
            >
              <div className="flex flex-col justify-center pb-1.25 w-full mb-10">
                <h2
                  className="text-foreground w-full"
                  style={{
                    ...typo.display,
                    fontWeight: "var(--font-weight-medium)",
                    lineHeight: "var(--line-height-display)",
                    letterSpacing: "var(--letter-spacing-display-title)",
                    textWrap: "balance",
                  }}
                >
                  Agentic Fleet Session
                </h2>
                <p
                  className="text-muted-foreground w-full"
                  style={{
                    ...typo.display,
                    fontWeight: "var(--font-weight-regular)",
                    letterSpacing: "var(--letter-spacing-display-subtitle)",
                    textWrap: "balance",
                  }}
                >
                  What do you need ?
                </p>
              </div>

              <div
                className="flex flex-wrap items-center justify-start gap-3 w-full"
                aria-live="polite"
                aria-label="Suggestion actions"
              >
                {suggestions.map((s, i) => (
                  <SuggestionChip
                    key={s.text}
                    icon={s.Icon}
                    label={s.text}
                    index={i}
                    onClick={onSuggestionClick}
                  />
                ))}
              </div>

              {hasHistory && !showHistory && (
                <motion.div
                  className="w-full mt-6"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={
                    prefersReduced ? { duration: 0.01 } : { delay: 0.35 }
                  }
                >
                  <button
                    type="button"
                    className="flex items-center gap-2 px-4 py-2.5 rounded-button border-subtle hover:border-border-strong hover:bg-secondary/60 transition-colors"
                    onClick={onToggleHistory}
                  >
                    <Clock
                      className="size-4 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span className="text-muted-foreground" style={typo.label}>
                      View recent conversations
                    </span>
                  </button>
                </motion.div>
              )}
            </ConversationEmptyState>
          </motion.div>
        )}

        {messages.length === 0 && (
          <AnimatePresence>{historyPanel}</AnimatePresence>
        )}

        {displayItems.map((displayItem) => {
          if (displayItem.kind === "tool_session") {
            return (
              <motion.div key={displayItem.key} {...preset}>
                {renderToolSession(displayItem)}
              </motion.div>
            );
          }

          if (displayItem.kind === "assistant_turn") {
            return (
              <motion.div key={displayItem.key} {...preset}>
                {renderAssistantTurn(displayItem)}
              </motion.div>
            );
          }

          const msg =
            displayItem.kind === "trace_message"
              ? {
                  ...displayItem.message,
                  renderParts: displayItem.renderParts,
                }
              : displayItem.message;

          return (
            <motion.div key={displayItem.key} {...preset}>
              {msg.type === "system" && (
                <div className="flex items-center gap-4 py-4">
                  <div className="h-[0.5px] flex-1 bg-border-strong opacity-20" />
                  <span
                    className="text-muted-foreground shrink-0 uppercase tracking-[0.2em] opacity-40 whitespace-pre-line"
                    style={{
                      ...typo.micro,
                      fontWeight: "var(--font-weight-semibold)",
                    }}
                  >
                    {msg.content}
                  </span>
                  <div className="h-[0.5px] flex-1 bg-border-strong opacity-20" />
                </div>
              )}

              {msg.type === "user" && (
                <Message from="user" className="mb-4">
                  <MessageContent className="max-w-[85%] md:max-w-lg rounded-xl border-subtle/80 bg-card/70 px-3.5 py-2.5">
                    <div className="text-[13px] leading-relaxed text-foreground whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </MessageContent>
                </Message>
              )}

              {(msg.type === "assistant" ||
                msg.type === "trace" ||
                msg.type === "reasoning") && (
                <Message from="assistant" className="mb-4">
                  <MessageContent className="w-full space-y-2.5">
                    {msg.renderParts?.map((part, idx) =>
                      renderTracePart(part, `${msg.id}-${part.kind}-${idx}`),
                    )}
                    {msg.type === "assistant" && msg.content ? (
                      <div className="max-w-content rounded-[22px] border-subtle/80 px-4 py-3.5 shadow-sm md:px-5 md:py-4">
                        <MessageResponse>
                          {msg.content}
                        </MessageResponse>
                      </div>
                    ) : null}
                    {msg.type === "assistant" &&
                    msg.streaming &&
                    !msg.content ? (
                      <div className="max-w-content rounded-[22px] border-subtle/80 px-4 py-3.5 md:px-5 md:py-4">
                        <Shimmer
                          as="span"
                          className="text-sm text-muted-foreground"
                        >
                          Loading
                        </Shimmer>
                      </div>
                    ) : null}
                  </MessageContent>
                </Message>
              )}

              {msg.type === "hitl" && msg.hitlData && (
                <div className="mb-6">
                  <Confirmation
                    state={mapConfirmationState(hitlConfirmationState(msg))}
                    approval={{
                      id: msg.id,
                      approved: hitlConfirmationState(msg) === "approved" ? true : hitlConfirmationState(msg) === "rejected" ? false : undefined,
                    }}
                  >
                    <ConfirmationTitle className="text-sm font-medium">
                      Checkpoint
                    </ConfirmationTitle>
                    <div className="mt-2 text-sm text-muted-foreground">
                      {msg.hitlData.question}
                    </div>
                    <ConfirmationRequest>
                      <ConfirmationActions>
                        {msg.hitlData.actions.map((action) => (
                          <ConfirmationAction
                            key={action.label}
                            onClick={() => onResolveHitl(msg.id, action.label)}
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
                      <div className="mt-3 text-xs text-primary">
                        Resolved: {msg.hitlData.resolvedLabel ?? "Approved"}
                      </div>
                    </ConfirmationAccepted>
                    <ConfirmationRejected>
                      <div className="mt-3 text-xs text-destructive">
                        Resolved: {msg.hitlData.resolvedLabel ?? "Rejected"}
                      </div>
                    </ConfirmationRejected>
                  </Confirmation>
                </div>
              )}

              {msg.type === "clarification" && msg.clarificationData && (
                <div className="mb-8">
                  <ClarificationCard
                    data={msg.clarificationData}
                    onResolve={(answer) =>
                      onResolveClarification(msg.id, answer)
                    }
                  />
                </div>
              )}

              {msg.type === "reasoning" &&
                msg.reasoningData &&
                !msg.renderParts?.length && (
                  <div className="mb-4">
                    <Reasoning
                      isStreaming={msg.reasoningData.isThinking}
                      duration={msg.reasoningData.duration}
                    >
                      <ReasoningTrigger />
                      <ReasoningContent>
                        {msg.reasoningData.parts.map(p => p.text).join("\n")}
                      </ReasoningContent>
                    </Reasoning>
                  </div>
                )}

              {msg.type === "plan_update" &&
                renderLegacyStatusCard(msg.content, "accent")}
              {msg.type === "rlm_executing" &&
                renderLegacyStatusCard(msg.content, "primary")}
              {msg.type === "memory_update" &&
                renderLegacyStatusCard(msg.content, "success")}
            </motion.div>
          );
        })}

        {showTypingShimmer && (
          <Message from="assistant" className="pt-2">
            <MessageContent className="max-w-xl">
              <Shimmer as="span" className="text-sm text-muted-foreground">
                Loading
              </Shimmer>
            </MessageContent>
          </Message>
        )}
      </ConversationContent>

      <ConversationScrollButton />
    </Conversation>
  );
}
