import type { AgentToolPart } from "@/lib/workspace/agent-tool-parts";
import { chatRenderPartToAgentToolPart, stableToolCallId } from "@/lib/workspace/agent-tool-parts";
import type {
  QuestionAnswer,
  QuestionConfig,
} from "@/components/agent-elements/question/question-prompt";
import type { ChatMessage, ChatRenderPart } from "@/lib/workspace/workspace-types";

export type { AgentToolPart };

interface AgentChatMessageAdapterOptions {
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
}

function toUiMessage(message: {
  id: string;
  role: "user" | "assistant";
  parts: unknown[];
}) {
  return message as import("ai").UIMessage;
}

function tracePartToAgentParts(part: ChatRenderPart, messageId: string, index: number): unknown[] {
  if (
    part.kind === "reasoning" ||
    part.kind === "tool" ||
    part.kind === "sandbox" ||
    part.kind === "task" ||
    part.kind === "queue" ||
    part.kind === "status_note" ||
    part.kind === "environment_variables"
  ) {
    const agentPart = chatRenderPartToAgentToolPart(part, messageId, index);
    return agentPart ? [agentPart] : [];
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
) {
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
