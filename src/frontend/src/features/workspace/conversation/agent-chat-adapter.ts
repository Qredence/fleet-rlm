import type { UIMessage } from "ai";

import type {
  QuestionAnswer,
  QuestionConfig,
} from "@/components/agent-elements/question/question-prompt";
import type {
  ChatMessage,
  ChatRenderPart,
  ChatRenderToolState,
} from "@/lib/workspace/workspace-types";

type AgentToolState = "input-streaming" | "call" | "output-available" | "output-error";

type AgentToolPart = {
  type: string;
  toolCallId: string;
  state: AgentToolState;
  input?: unknown;
  output?: unknown;
};

interface AgentChatMessageAdapterOptions {
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
}

function toUiMessage(message: {
  id: string;
  role: "user" | "assistant";
  parts: unknown[];
}): UIMessage {
  return message as UIMessage;
}

function mapToolState(state: ChatRenderToolState): AgentToolState {
  switch (state) {
    case "input-streaming":
      return "input-streaming";
    case "running":
      return "call";
    case "output-available":
      return "output-available";
    case "output-error":
      return "output-error";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeToolInput(toolType: string, input: unknown): Record<string, unknown> {
  const base = isRecord(input) ? { ...input } : {};
  const normalized = toolType.toLowerCase();

  if (
    /(load[_-]?document|read[_-]?(?:file|document)(?:[_-]?slice)?|open[_-]?document|document[_-]?read|file[_-]?read)/.test(
      normalized,
    )
  ) {
    const filePath =
      (typeof base.file_path === "string" && base.file_path.trim()) ||
      (typeof base.path === "string" && base.path.trim()) ||
      (typeof base.document === "string" && base.document.trim()) ||
      "";
    if (filePath && !base.file_path) base.file_path = filePath;
  }

  if (normalized.includes("glob") || normalized.includes("list")) {
    const pattern =
      (typeof base.pattern === "string" && base.pattern.trim()) ||
      (typeof base.path === "string" && base.path.trim()) ||
      (typeof base.query === "string" && base.query.trim()) ||
      "";
    if (pattern && !base.pattern) base.pattern = pattern;
  }

  if (/(grep|search|find)/.test(normalized)) {
    const query =
      (typeof base.query === "string" && base.query.trim()) ||
      (typeof base.pattern === "string" && base.pattern.trim()) ||
      (typeof base.path === "string" && base.path.trim()) ||
      "";
    if (query && !base.query) base.query = query;
  }

  return base;
}

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function sanitizeToolName(value: string): string {
  const compact = value
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join("");
  return compact || "Tool";
}

function stableToolCallId(messageId: string, kind: string, index: number, stepIndex?: number) {
  const suffix = stepIndex == null ? index : stepIndex;
  return `${messageId}:${kind}:${suffix}`;
}

function toolPartType(toolType: string): string {
  const normalized = toolType.toLowerCase();
  if (normalized.startsWith("mcp__")) {
    return `tool-${toolType}`;
  }
  if (/(bash|exec|command|terminal|run|shell|python|repl|interpreter|sandbox)/.test(normalized)) {
    return "tool-Bash";
  }
  if (
    /(load[_-]?document|read[_-]?(?:file|document)(?:[_-]?slice)?|open[_-]?document|document[_-]?read|file[_-]?read)/.test(
      normalized,
    )
  ) {
    return "tool-Read";
  }
  if (
    /(list[_-]?files?|list[_-]?dir|glob|tree|ls|directory[_-]?listing|browse[_-]?files?)/.test(
      normalized,
    )
  ) {
    return "tool-Glob";
  }
  if (/(write|create_file)/.test(normalized)) return "tool-Write";
  if (/(edit|patch|notebook)/.test(normalized)) return "tool-Edit";
  if (/(grep|find|search)/.test(normalized)) {
    return normalized.includes("web") ? "tool-WebSearch" : "tool-Grep";
  }
  if (/(webfetch|fetch|url|browser)/.test(normalized)) return "tool-WebFetch";
  if (/(todo|task_list)/.test(normalized)) return "tool-TodoWrite";
  if (/(plan|planning)/.test(normalized)) return "tool-PlanWrite";
  if (/(delegate|sub_rlm|agent|recursive)/.test(normalized)) return "tool-Agent";
  if (/(think|reason)/.test(normalized)) return "tool-Thinking";
  return `tool-${sanitizeToolName(toolType)}`;
}

function commandInput(part: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>) {
  if (part.kind === "sandbox") {
    return {
      command: part.code || part.title,
      description: part.title,
      language: part.language,
    };
  }
  if (isRecord(part.input)) return normalizeToolInput(part.toolType, part.input);
  const input = stringifyValue(part.input);
  return {
    command: input || part.title || part.toolType,
    description: part.title,
  };
}

function outputRecord(part: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>) {
  if (part.errorText) return { error: part.errorText };
  if (part.kind === "sandbox") return part.output ? { stdout: part.output } : undefined;
  if (isRecord(part.output)) return part.output;
  const output = stringifyValue(part.output);
  return output ? { result: output } : undefined;
}

function tracePartToAgentParts(part: ChatRenderPart, messageId: string, index: number): unknown[] {
  if (part.kind === "reasoning") {
    const text = part.parts.map((item) => item.text).join("\n");
    if (!text.trim()) return [];
    return [
      {
        type: "tool-Thinking",
        toolCallId: stableToolCallId(messageId, "reasoning", index),
        state: part.isStreaming ? "input-streaming" : "output-available",
        input: { thought: text, label: part.label ?? "Reasoning" },
        output: part.isStreaming ? undefined : { reasoning: text },
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "chain_of_thought") {
    return part.steps.map(
      (step, stepIndex) =>
        ({
          type: "tool-Thinking",
          toolCallId: stableToolCallId(messageId, "trajectory", stepIndex, step.index),
          state:
            step.status === "active" || step.status === "pending" ? "call" : "output-available",
          input: {
            thought: [step.body, ...(step.details ?? [])].filter(Boolean).join("\n"),
            label: step.label,
          },
          output:
            step.status === "active" || step.status === "pending"
              ? undefined
              : { reasoning: [step.body, ...(step.details ?? [])].filter(Boolean).join("\n") },
        }) satisfies AgentToolPart,
    );
  }

  if (part.kind === "tool" || part.kind === "sandbox") {
    const toolType = part.kind === "sandbox" ? "sandbox" : part.toolType;
    return [
      {
        type: toolPartType(toolType),
        toolCallId: stableToolCallId(messageId, toolType, index, part.stepIndex),
        state: mapToolState(part.state),
        input: commandInput(part),
        output: outputRecord(part),
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "task") {
    return [
      {
        type: "tool-TodoWrite",
        toolCallId: stableToolCallId(messageId, "task", index),
        state:
          part.status === "in_progress"
            ? "call"
            : part.status === "error"
              ? "output-error"
              : "output-available",
        input: {
          action: "update",
          title: part.title,
          todos: part.items?.map((item) => ({
            content: item.text,
            status: part.status,
            file: item.file?.name,
          })),
        },
        output: part.status === "in_progress" ? undefined : { status: part.status },
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "queue") {
    return [
      {
        type: "tool-PlanWrite",
        toolCallId: stableToolCallId(messageId, "plan", index),
        state: part.items.every((item) => item.completed) ? "output-available" : "call",
        input: {
          action: "update",
          plan: {
            title: part.title,
            steps: part.items.map((item) => ({
              content: item.label,
              description: item.description,
              status: item.completed ? "completed" : "pending",
            })),
          },
        },
        output: part.items.every((item) => item.completed) ? { status: "completed" } : undefined,
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "status_note") {
    return [
      {
        type: "tool-Status",
        toolCallId: stableToolCallId(messageId, "status", index, part.stepIndex),
        state: part.tone === "error" ? "output-error" : "output-available",
        input: { message: part.text, tone: part.tone },
        output: { message: part.text, tone: part.tone },
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "environment_variables") {
    return [
      {
        type: "tool-EnvironmentVariables",
        toolCallId: stableToolCallId(messageId, "env", index),
        state: "output-available",
        input: { title: part.title ?? "Environment variables" },
        output: {
          variables: part.variables.map((variable) => ({
            name: variable.name,
            value: variable.value,
            required: variable.required,
          })),
        },
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "confirmation") {
    const question: QuestionConfig = {
      kind: "single",
      title: part.question,
      options: part.actions?.map((action) => ({
        id: action.label,
        label: action.label,
      })),
    };
    return [
      {
        type: "tool-Question",
        toolCallId: stableToolCallId(messageId, "confirmation", index),
        state: part.state === "approval-requested" ? "call" : "output-available",
        input: {
          questions: [question],
          submitLabel: "Send",
          allowSkip: false,
        },
        output:
          part.state === "approval-requested"
            ? undefined
            : {
                answer: {
                  kind: "single",
                  selectedIds: [part.state === "approved" ? "Approved" : "Rejected"],
                  text: part.state === "approved" ? "Approved" : "Rejected",
                } satisfies QuestionAnswer,
              },
      } satisfies AgentToolPart,
    ];
  }

  if (part.kind === "inline_citation_group" || part.kind === "sources") {
    const title = part.kind === "sources" ? (part.title ?? "Sources") : "Sources";
    const lines =
      part.kind === "sources"
        ? part.sources.map((source) => `- ${source.title}${source.url ? `: ${source.url}` : ""}`)
        : part.citations.map((citation) => `- ${citation.title}: ${citation.url}`);
    return [{ type: "text", text: `**${title}**\n${lines.join("\n")}` }];
  }

  if (part.kind === "attachments") {
    return part.attachments.map((attachment) => ({
      type: "file",
      filename: attachment.name,
      mimeType: attachment.mimeType ?? attachment.mediaType ?? "application/octet-stream",
      url: attachment.url ?? attachment.previewUrl ?? "",
      size: attachment.sizeBytes,
    }));
  }

  return [];
}

function optionLabelById(question: QuestionConfig, id: string) {
  return question.options?.find((option) => option.id === id)?.label ?? id;
}

function answerText(question: QuestionConfig, answer: QuestionAnswer) {
  if (answer.kind === "skip") return "";
  if (answer.kind === "text") return answer.text ?? "";
  const labels = (answer.selectedIds ?? []).map((id) => optionLabelById(question, id));
  return [labels.join(", "), answer.text].filter(Boolean).join(" - ");
}

function hitlToQuestionPart(
  message: ChatMessage,
  onResolveHitl: AgentChatMessageAdapterOptions["onResolveHitl"],
): AgentToolPart | null {
  if (!message.hitlData) return null;
  const question: QuestionConfig = {
    kind: "single",
    title: message.hitlData.question,
    options: message.hitlData.actions.map((action) => ({
      id: action.label,
      label: action.label,
    })),
  };
  const resolvedAnswer: QuestionAnswer | undefined = message.hitlData.resolved
    ? {
        kind: "single",
        selectedIds: message.hitlData.resolvedLabel ? [message.hitlData.resolvedLabel] : [],
        text: message.hitlData.resolvedLabel,
      }
    : undefined;
  return {
    type: "tool-Question",
    toolCallId: message.id,
    state: resolvedAnswer ? "output-available" : "call",
    input: {
      questions: [question],
      submitLabel: "Send",
      skipLabel: "Reject",
      allowSkip: false,
      onSubmitAnswer: (answer: QuestionAnswer) => {
        const text = answerText(question, answer);
        if (text) onResolveHitl(message.id, text);
      },
    },
    output: resolvedAnswer ? { answer: resolvedAnswer } : undefined,
  };
}

function clarificationToQuestionPart(
  message: ChatMessage,
  onResolveClarification: AgentChatMessageAdapterOptions["onResolveClarification"],
): AgentToolPart | null {
  if (!message.clarificationData) return null;
  const hasOptions = message.clarificationData.options.length > 0;
  const question: QuestionConfig = hasOptions
    ? {
        kind: "single",
        title: message.clarificationData.question,
        description: message.clarificationData.stepLabel,
        options: message.clarificationData.options.map((option) => ({
          id: option.id,
          label: option.label,
          description: option.description,
        })),
        allowCustom: true,
        customLabel: "Other",
        customPlaceholder: "Type another answer",
      }
    : {
        kind: "text",
        title: message.clarificationData.question,
        description: message.clarificationData.stepLabel,
        placeholder: "Type your clarification",
      };
  const resolvedAnswer: QuestionAnswer | undefined = message.clarificationData.resolved
    ? hasOptions
      ? {
          kind: "single",
          selectedIds: message.clarificationData.resolvedAnswer
            ? [message.clarificationData.resolvedAnswer]
            : [],
          text: message.clarificationData.resolvedAnswer,
        }
      : {
          kind: "text",
          text: message.clarificationData.resolvedAnswer,
        }
    : undefined;
  return {
    type: "tool-Question",
    toolCallId: message.id,
    state: resolvedAnswer ? "output-available" : "call",
    input: {
      questions: [question],
      submitLabel: "Send",
      skipLabel: "Skip",
      allowSkip: false,
      onSubmitAnswer: (answer: QuestionAnswer) => {
        const text = answerText(question, answer);
        if (text) onResolveClarification(message.id, text);
      },
    },
    output: resolvedAnswer ? { answer: resolvedAnswer } : undefined,
  };
}

function messageToParts(message: ChatMessage, options: AgentChatMessageAdapterOptions): unknown[] {
  const parts: unknown[] = [];
  for (const [index, part] of (message.renderParts ?? []).entries()) {
    parts.push(...tracePartToAgentParts(part, message.id, index));
  }
  if (message.content && message.type !== "trace" && message.type !== "reasoning") {
    parts.unshift({ type: "text", text: message.content });
  }
  if (message.type === "reasoning" && message.reasoningData && parts.length === 0) {
    const text = message.reasoningData.parts.map((part) => part.text).join("\n");
    if (text) {
      parts.push({
        type: "tool-Thinking",
        toolCallId: message.id,
        state: message.reasoningData.isThinking ? "call" : "output-available",
        input: { thought: text },
        output: message.reasoningData.isThinking ? undefined : { reasoning: text },
      } satisfies AgentToolPart);
    }
  }
  if (message.type === "hitl") {
    const questionPart = hitlToQuestionPart(message, options.onResolveHitl);
    if (questionPart) return [questionPart];
  }
  if (message.type === "clarification") {
    const questionPart = clarificationToQuestionPart(message, options.onResolveClarification);
    if (questionPart) return [questionPart];
  }
  return parts;
}

export function toAgentChatMessages(
  messages: ChatMessage[],
  options: AgentChatMessageAdapterOptions,
): UIMessage[] {
  return messages.flatMap((message) => {
    if (message.type === "user") {
      return [
        toUiMessage({
          id: message.id,
          role: "user",
          parts: [{ type: "text", text: message.content }],
        }),
      ];
    }

    const parts = messageToParts(message, options);
    if (parts.length === 0) return [];
    return [
      toUiMessage({
        id: message.id,
        role: "assistant",
        parts,
      }),
    ];
  });
}
