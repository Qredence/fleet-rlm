import type { WsServerEvent, WsServerMessage } from "@/lib/rlm-api";
import type { ChatMessage } from "@/lib/data/types";
import { createLocalId } from "@/lib/id";
import { QueryClient } from "@tanstack/react-query";

const DEFAULT_PHASE = 1 as const;

interface ApplyFrameResult {
  messages: ChatMessage[];
  terminal: boolean;
  errored: boolean;
}

function nextId(prefix: string): string {
  return createLocalId(prefix);
}

function appendSystem(messages: ChatMessage[], text: string): ChatMessage[] {
  if (!text.trim()) return messages;
  return [
    ...messages,
    {
      id: nextId("sys"),
      type: "system",
      content: text,
      phase: DEFAULT_PHASE,
    },
  ];
}

function latestStreamingAssistantIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg) continue;
    if (msg.type === "assistant" && msg.streaming) return i;
  }
  return -1;
}

function latestOpenReasoningIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg) continue;
    if (msg.type === "reasoning" && msg.reasoningData?.isThinking) return i;
  }
  return -1;
}

function ensureStreamingAssistant(messages: ChatMessage[]): ChatMessage[] {
  if (latestStreamingAssistantIndex(messages) >= 0) return messages;
  return [
    ...messages,
    {
      id: nextId("assistant"),
      type: "assistant",
      content: "",
      streaming: true,
      phase: DEFAULT_PHASE,
    },
  ];
}

function appendAssistantToken(
  messages: ChatMessage[],
  token: string,
): ChatMessage[] {
  if (!token) return messages;
  const withAssistant = ensureStreamingAssistant(messages);
  const idx = latestStreamingAssistantIndex(withAssistant);
  if (idx < 0) return withAssistant;

  const copy = [...withAssistant];
  const target = copy[idx];
  if (!target) return withAssistant;
  copy[idx] = {
    ...target,
    content: `${target.content}${token}`,
  };
  return copy;
}

function appendReasoning(messages: ChatMessage[], text: string): ChatMessage[] {
  const trimmed = text.trim();
  if (!trimmed) return messages;

  const idx = latestOpenReasoningIndex(messages);
  if (idx >= 0) {
    const msg = messages[idx];
    if (!msg || !msg.reasoningData) return messages;

    const copy = [...messages];
    copy[idx] = {
      ...msg,
      reasoningData: {
        ...msg.reasoningData,
        parts: [...msg.reasoningData.parts, { type: "text", text: trimmed }],
        isThinking: true,
      },
    };
    return copy;
  }

  return [
    ...messages,
    {
      id: nextId("reasoning"),
      type: "reasoning",
      content: "",
      phase: DEFAULT_PHASE,
      reasoningData: {
        parts: [{ type: "text", text: trimmed }],
        isThinking: true,
      },
    },
  ];
}

function finishReasoning(messages: ChatMessage[]): ChatMessage[] {
  let updated = false;
  const next = messages.map((msg) => {
    if (
      msg.type !== "reasoning" ||
      !msg.reasoningData ||
      !msg.reasoningData.isThinking
    ) {
      return msg;
    }

    updated = true;
    return {
      ...msg,
      reasoningData: {
        ...msg.reasoningData,
        isThinking: false,
      },
    };
  });

  return updated ? next : messages;
}

function completeAssistant(
  messages: ChatMessage[],
  text: string,
): ChatMessage[] {
  const idx = latestStreamingAssistantIndex(messages);

  if (idx >= 0) {
    const copy = [...messages];
    const current = copy[idx];
    if (!current) return messages;
    copy[idx] = {
      ...current,
      content: text || current.content,
      streaming: false,
    };
    return copy;
  }

  if (!text.trim()) return messages;

  return [
    ...messages,
    {
      id: nextId("assistant"),
      type: "assistant",
      content: text,
      streaming: false,
      phase: DEFAULT_PHASE,
    },
  ];
}

function readGuardrailWarnings(
  payload: Record<string, unknown> | undefined,
): string[] {
  const raw = payload?.guardrail_warnings;
  if (!Array.isArray(raw)) return [];

  return raw
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function applyEvent(
  messages: ChatMessage[],
  frame: WsServerEvent,
  queryClient?: QueryClient,
): ApplyFrameResult {
  const { kind, text, payload } = frame.data;

  switch (kind) {
    case "assistant_token": {
      return {
        messages: appendAssistantToken(messages, text),
        terminal: false,
        errored: false,
      };
    }

    case "reasoning_step": {
      return {
        messages: appendReasoning(messages, text),
        terminal: false,
        errored: false,
      };
    }

    case "status": {
      return {
        messages: appendReasoning(messages, `Status: ${text}`),
        terminal: false,
        errored: false,
      };
    }

    case "tool_call": {
      return {
        messages: appendReasoning(messages, `Tool Call: ${text}`),
        terminal: false,
        errored: false,
      };
    }

    case "tool_result": {
      return {
        messages: appendReasoning(messages, `Tool Result: ${text}`),
        terminal: false,
        errored: false,
      };
    }

    case "plan_update": {
      let next = finishReasoning(messages);
      next = [
        ...next,
        {
          id: nextId("plan"),
          type: "plan_update",
          content: text || "Running plan...",
          phase: DEFAULT_PHASE,
        },
      ];
      return {
        messages: next,
        terminal: false,
        errored: false,
      };
    }

    case "rlm_executing": {
      let next = finishReasoning(messages);
      const toolName = payload?.tool_name || "Sub-agent iteration";
      next = [
        ...next,
        {
          id: nextId("rlm"),
          type: "rlm_executing",
          content: `Executing ${toolName}...`,
          phase: DEFAULT_PHASE,
        },
      ];
      return {
        messages: next,
        terminal: false,
        errored: false,
      };
    }

    case "memory_update": {
      let next = finishReasoning(messages);
      next = [
        ...next,
        {
          id: nextId("memory"),
          type: "memory_update",
          content: text || "Updating memory...",
          phase: DEFAULT_PHASE,
        },
      ];

      // Attempt to invalidate TanStack query if queryClient is passed
      if (queryClient) {
        queryClient.invalidateQueries({ queryKey: ["memory"] });
      }

      return {
        messages: next,
        terminal: false,
        errored: false,
      };
    }

    case "final": {
      let next = completeAssistant(messages, text);
      next = finishReasoning(next);

      const finalReasoning =
        typeof payload?.final_reasoning === "string"
          ? payload.final_reasoning.trim()
          : "";

      if (finalReasoning) {
        next = appendReasoning(next, `Final reasoning: ${finalReasoning}`);
        next = finishReasoning(next);
      }

      const warnings = readGuardrailWarnings(payload);
      if (warnings.length > 0) {
        next = appendSystem(
          next,
          `Guardrail warnings:\n- ${warnings.join("\n- ")}`,
        );
      }

      return {
        messages: next,
        terminal: true,
        errored: false,
      };
    }

    case "cancelled": {
      let next = finishReasoning(messages);
      next = appendSystem(next, text || "Request cancelled.");
      return {
        messages: next,
        terminal: true,
        errored: false,
      };
    }

    case "error": {
      let next = finishReasoning(messages);
      next = appendSystem(
        next,
        `Backend error: ${text || "Unknown server error."}`,
      );
      return {
        messages: next,
        terminal: true,
        errored: true,
      };
    }

    default: {
      return {
        messages,
        terminal: false,
        errored: false,
      };
    }
  }
}

export function applyWsFrameToMessages(
  messages: ChatMessage[],
  frame: WsServerMessage,
  queryClient?: QueryClient,
): ApplyFrameResult {
  if (frame.type === "error") {
    const next = appendSystem(messages, `Backend error: ${frame.message}`);
    return {
      messages: finishReasoning(next),
      terminal: true,
      errored: true,
    };
  }

  return applyEvent(messages, frame, queryClient);
}
