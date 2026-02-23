import { type RefObject, type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { Clock } from "lucide-react";
import type { ChatMessage, ChatRenderPart } from "@/lib/data/types";
import { typo } from "@/lib/config/typo";
import { cn } from "@/components/ui/utils";
import { Streamdown } from "@/components/ui/streamdown";
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
import { Reasoning } from "@/components/ai-elements/reasoning";
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
} from "@/components/ai-elements/queue";
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
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { ClarificationCard } from "@/features/ClarificationCard";
import { SuggestionChip } from "@/components/ui/suggestion-chip";
import {
  SuggestionIconBolt,
  SuggestionIconTune,
  SuggestionIconSparkle,
} from "@/components/shared/SuggestionIcons";
import {
  fadeUp,
  fadeUpReduced,
} from "@/app/pages/skill-creation/animation-presets";

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
                  className="space-y-2 rounded-md border border-border-subtle p-2"
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

function renderTracePart(part: ChatRenderPart, key: string) {
  switch (part.kind) {
    case "reasoning":
      return (
        <Reasoning
          key={key}
          parts={part.parts}
          isStreaming={part.isStreaming}
          duration={part.duration}
          className="w-full"
        />
      );
    case "chain_of_thought":
      return (
        <ChainOfThought key={key} defaultOpen>
          <ChainOfThoughtHeader>
            {part.title ?? "Execution trace"}
          </ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            <div className="divide-y divide-border-subtle">
              {part.steps.map((step) => (
                <ChainOfThoughtStep
                  key={step.id}
                  label={step.label}
                  status={step.status}
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
        <Task key={key} defaultOpen={part.status === "in_progress"}>
          <TaskTrigger title={part.title} status={part.status} />
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
        <Tool key={key} defaultOpen={part.state !== "running"}>
          <ToolHeader
            toolType={part.title || part.toolType}
            state={part.state}
          />
          <ToolContent>
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
        <Sandbox key={key} defaultOpen={part.state !== "running"}>
          <SandboxHeader title={part.title} state={part.state} />
          <SandboxContent>
            <SandboxTabs defaultValue="output">
              <SandboxTabsBar>
                <SandboxTabsList>
                  <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
                  <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                </SandboxTabsList>
              </SandboxTabsBar>
              <SandboxTabContent value="output">
                {part.errorText ? (
                  <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                    {part.errorText}
                  </div>
                ) : output ? (
                  <Streamdown content={output} streaming={false} />
                ) : (
                  <div className="text-xs text-muted-foreground">
                    No output yet
                  </div>
                )}
              </SandboxTabContent>
              <SandboxTabContent value="code">
                {code ? (
                  <pre className="overflow-x-auto rounded-md border border-border-subtle bg-muted/30 p-2 text-xs">
                    <code>{code}</code>
                  </pre>
                ) : (
                  <div className="text-xs text-muted-foreground">
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
                required={variable.required}
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
    case "status_note":
      return (
        <div
          key={key}
          className={cn(
            "rounded-lg border px-3 py-2 text-xs",
            part.tone === "error" &&
              "border-destructive/30 bg-destructive/5 text-destructive",
            part.tone === "warning" &&
              "border-amber-300/40 bg-amber-50/50 text-amber-900",
            part.tone === "success" &&
              "border-emerald-300/30 bg-emerald-50/30 text-emerald-900",
            (!part.tone || part.tone === "neutral") &&
              "border-border-subtle bg-card text-muted-foreground",
          )}
        >
          {part.text}
        </div>
      );
    case "confirmation":
      return (
        <Confirmation key={key} state={part.state}>
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
  colorClasses: string,
  pulseClasses: string,
) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border p-3",
        colorClasses,
      )}
    >
      <div className={cn("size-2 rounded-full animate-pulse", pulseClasses)} />
      <span style={typo.label}>{content}</span>
    </div>
  );
}

export function ChatMessageList({
  messages,
  isTyping,
  isMobile,
  scrollRef,
  contentRef,
  isAtBottom,
  scrollToBottom,
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
  const showTypingShimmer = isTyping && !hasStreamingAssistant;

  return (
    <Conversation
      scrollRef={scrollRef}
      contentRef={contentRef}
      isAtBottom={isAtBottom}
      scrollToBottom={scrollToBottom}
      className="bg-background"
    >
      <ConversationContent>
        {messages.length === 0 && (
          <motion.div {...preset}>
            <ConversationEmptyState
              title="Agentic Fleet Session"
              description="What do you need ?"
              icon={<span aria-hidden className="hidden" />}
              className={cn(
                "pt-16 pb-8 text-left items-start",
                isMobile && "pt-10",
              )}
            >
              <div className="flex flex-col justify-center pb-[5px] w-full mb-10">
                <h2
                  className="text-foreground w-full"
                  style={{
                    ...typo.display,
                    fontWeight: "var(--font-weight-medium)",
                    lineHeight: "40px",
                    letterSpacing: "-0.53px",
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
                    letterSpacing: "-1.6px",
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
                    className="flex items-center gap-2 px-4 py-2.5 rounded-button border border-border-subtle hover:border-border-strong hover:bg-secondary/60 transition-colors"
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

        {messages.map((msg) => (
          <motion.div key={msg.id} {...preset}>
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
                <MessageContent className="max-w-[85%] md:max-w-md rounded-xl border border-border-subtle bg-card px-4 py-3 shadow-sm">
                  <div className="text-sm text-foreground whitespace-pre-wrap">
                    {msg.content}
                  </div>
                </MessageContent>
              </Message>
            )}

            {(msg.type === "assistant" || msg.type === "trace") && (
              <Message from="assistant" className="mb-4">
                <MessageContent className="w-full space-y-3">
                  {msg.renderParts?.map((part, idx) =>
                    renderTracePart(part, `${msg.id}-${part.kind}-${idx}`),
                  )}
                  {msg.type === "assistant" && msg.content ? (
                    <MessageResponse streaming={msg.streaming}>
                      {msg.content}
                    </MessageResponse>
                  ) : null}
                  {msg.type === "assistant" && msg.streaming && !msg.content ? (
                    <div className="max-w-xl">
                      <Shimmer lines={3} />
                    </div>
                  ) : null}
                </MessageContent>
              </Message>
            )}

            {msg.type === "hitl" && msg.hitlData && (
              <div className="mb-6">
                <Confirmation state={hitlConfirmationState(msg)}>
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
                    <div className="mt-3 text-xs text-emerald-600">
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
                  onResolve={(answer) => onResolveClarification(msg.id, answer)}
                />
              </div>
            )}

            {msg.type === "reasoning" &&
              msg.reasoningData &&
              !msg.renderParts?.length && (
                <div className="mb-4">
                  <Reasoning
                    parts={msg.reasoningData.parts}
                    isStreaming={msg.reasoningData.isThinking}
                    duration={msg.reasoningData.duration}
                  />
                </div>
              )}

            {msg.type === "plan_update" &&
              renderLegacyStatusCard(
                msg.content,
                "border-accent/20 bg-accent/5 text-accent",
                "bg-accent",
              )}
            {msg.type === "rlm_executing" &&
              renderLegacyStatusCard(
                msg.content,
                "border-primary/20 bg-primary/5 text-primary",
                "bg-primary",
              )}
            {msg.type === "memory_update" &&
              renderLegacyStatusCard(
                msg.content,
                "border-green-500/20 bg-green-500/5 text-green-500",
                "bg-green-500",
              )}
          </motion.div>
        ))}

        {showTypingShimmer && (
          <Message from="assistant" className="pt-2">
            <MessageContent className="max-w-xl">
              <Shimmer lines={3} />
            </MessageContent>
          </Message>
        )}
      </ConversationContent>

      <ConversationScrollButton />
    </Conversation>
  );
}
