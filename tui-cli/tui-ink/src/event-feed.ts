import type { TranscriptLine } from "./types.js";

export type EventFeedMode = "off" | "compact" | "verbose";

export interface EventFeedState {
  lines: TranscriptLine[];
  reasoningCount: number;
  moduleStatusCount: number;
}

export interface ChatEventInput {
  kind: string;
  text: string;
  payload: unknown;
  tokens?: number; // For assistant_token_batch events
}

export interface InlineEventResult {
  state: EventFeedState;
  line: string | null;
}

const MAX_LINES = 80;

export function initialEventFeedState(): EventFeedState {
  return {
    lines: [],
    reasoningCount: 0,
    moduleStatusCount: 0,
  };
}

export function reduceEventFeed(
  state: EventFeedState,
  mode: EventFeedMode,
  event: ChatEventInput,
  nextLine: (role: TranscriptLine["role"], text: string) => TranscriptLine,
): EventFeedState {
  if (mode === "off") {
    return state;
  }
  if (mode === "verbose") {
    const summary = summarizeEventForFeed(event.kind, event.text, event.payload);
    return {
      ...state,
      lines: [...state.lines, nextLine("status", `[${event.kind}] ${summary}`)].slice(-MAX_LINES),
    };
  }
  return reduceCompactEventFeed(state, event, nextLine);
}

function reduceCompactEventFeed(
  state: EventFeedState,
  event: ChatEventInput,
  nextLine: (role: TranscriptLine["role"], text: string) => TranscriptLine,
): EventFeedState {
  if (event.kind === "assistant_token" || event.kind === "assistant_token_batch") {
    return state;
  }
  if (event.kind === "reasoning_step") {
    return withMeta(state, { reasoningCount: state.reasoningCount + 1 }, nextLine);
  }
  if (event.kind === "status") {
    if (event.text.startsWith("Running module:")) {
      return withMeta(state, { moduleStatusCount: state.moduleStatusCount + 1 }, nextLine);
    }
    // Keep only unusual status messages in compact mode.
    const lowered = event.text.toLowerCase();
    if (!lowered.includes("error") && !lowered.includes("fallback")) {
      return state;
    }
  }
  if (event.kind === "tool_result") {
    const summary = summarizeEventForFeed(event.kind, event.text, event.payload).toLowerCase();
    if (summary === "tool result: finished") {
      return state;
    }
  }

  const compactSummary =
    event.kind === "final"
      ? summarizeFinalCompact(event.text)
      : summarizeEventForFeed(event.kind, event.text, event.payload);
  return {
    ...state,
    lines: [
      ...stripMetaLines(state.lines),
      nextLine("status", `[${event.kind}] ${compactSummary}`),
    ].slice(-MAX_LINES),
  };
}

function withMeta(
  state: EventFeedState,
  update: Partial<Pick<EventFeedState, "reasoningCount" | "moduleStatusCount">>,
  nextLine: (role: TranscriptLine["role"], text: string) => TranscriptLine,
): EventFeedState {
  const next = {
    reasoningCount: update.reasoningCount ?? state.reasoningCount,
    moduleStatusCount: update.moduleStatusCount ?? state.moduleStatusCount,
  };
  const lines = [
    ...stripMetaLines(state.lines),
    nextLine("status", `[meta] reasoning steps: ${next.reasoningCount}`),
    nextLine("status", `[meta] module status updates: ${next.moduleStatusCount}`),
  ];
  return {
    ...state,
    ...next,
    lines: lines.slice(-MAX_LINES),
  };
}

function stripMetaLines(lines: TranscriptLine[]): TranscriptLine[] {
  return lines.filter((line) => !line.text.startsWith("[meta] "));
}

export function clearEventFeed(state: EventFeedState): EventFeedState {
  return {
    ...state,
    lines: [],
    reasoningCount: 0,
    moduleStatusCount: 0,
  };
}

export function summarizeEventForFeed(kind: string, text: string, payload: unknown): string {
  if (kind === "reasoning_step") {
    return "reasoning step captured (details hidden)";
  }
  if (kind === "trajectory_step" && typeof payload === "object" && payload !== null) {
    const stepIndex = (payload as Record<string, unknown>).step_index;
    const stepData = (payload as Record<string, unknown>).step_data;
    if (typeof stepData === "object" && stepData !== null) {
      const toolName = (stepData as Record<string, unknown>).tool_name;
      if (typeof toolName === "string" && toolName.trim()) {
        return `trajectory step #${String(stepIndex ?? "?")}: tool=${toolName}`;
      }
    }
    return `trajectory step #${String(stepIndex ?? "?")}`;
  }
  if (text.trim()) {
    return text.trim();
  }
  try {
    return JSON.stringify(payload);
  } catch {
    return String(payload);
  }
}

function formatVerboseEvent(kind: string, text: string, payload: unknown): string {
  // In verbose mode, show full reasoning and trajectory thought content
  if (kind === "reasoning_step") {
    const trimmed = text.trim();
    if (trimmed) {
      return truncateIfNeeded(trimmed, 800);
    }
    return "reasoning step (no text)";
  }

  if (kind === "status") {
    const trimmed = text.trim();
    if (trimmed) {
      // Show all status messages in verbose mode
      return trimmed;
    }
  }

  if (kind === "trajectory_step" && typeof payload === "object" && payload !== null) {
    const stepIndex = (payload as Record<string, unknown>).step_index;
    const stepData = (payload as Record<string, unknown>).step_data;

    if (typeof stepData === "object" && stepData !== null) {
      const thought = (stepData as Record<string, unknown>).thought;
      const toolName = (stepData as Record<string, unknown>).tool_name;
      const input = (stepData as Record<string, unknown>).input;

      const parts: string[] = [`step #${String(stepIndex ?? "?")}`];

      if (typeof toolName === "string" && toolName.trim()) {
        parts.push(`tool=${toolName}`);
      }

      if (typeof thought === "string" && thought.trim()) {
        parts.push(`\n  thought: ${truncateIfNeeded(thought.trim(), 400)}`);
      }

      if (typeof input === "object" && input !== null) {
        const inputStr = JSON.stringify(input);
        if (inputStr.length < 200) {
          parts.push(`\n  input: ${inputStr}`);
        }
      }

      return parts.join(", ");
    }
    return `trajectory step #${String(stepIndex ?? "?")}`;
  }

  // For all other event types, use standard summarization
  return summarizeEventForFeed(kind, text, payload);
}

function truncateIfNeeded(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 3)}...`;
}

function summarizeFinalCompact(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) {
    return "assistant response completed";
  }
  if (trimmed.length <= 180) {
    return trimmed;
  }
  return `${trimmed.slice(0, 177)}...`;
}

export function reduceInlineEvent(
  state: EventFeedState,
  mode: EventFeedMode,
  event: ChatEventInput,
): InlineEventResult {
  if (mode === "off") {
    return { state, line: null };
  }

  if (mode === "verbose") {
    // Suppress final event duplication even in verbose mode
    if (event.kind === "final") {
      return { state, line: null };
    }
    // In verbose mode, show all reasoning, status, and tool events
    if (event.kind === "assistant_token" || event.kind === "assistant_token_batch") {
      return { state, line: null };
    }
    return {
      state,
      line: `[${event.kind}] ${formatVerboseEvent(event.kind, event.text, event.payload)}`,
    };
  }

  if (event.kind === "assistant_token" || event.kind === "assistant_token_batch") {
    return { state, line: null };
  }
  if (event.kind === "final") {
    // Final assistant answer already appears in transcript.
    return { state, line: null };
  }
  if (event.kind === "reasoning_step") {
    const next = { ...state, reasoningCount: state.reasoningCount + 1 };
    if (next.reasoningCount % 3 === 0) {
      return {
        state: next,
        line: `[meta] reasoning steps: ${next.reasoningCount}`,
      };
    }
    return { state: next, line: null };
  }
  if (event.kind === "status" && event.text.startsWith("Running module:")) {
    const next = { ...state, moduleStatusCount: state.moduleStatusCount + 1 };
    if (next.moduleStatusCount % 3 === 0) {
      return {
        state: next,
        line: `[meta] module status updates: ${next.moduleStatusCount}`,
      };
    }
    return { state: next, line: null };
  }
  if (event.kind === "status") {
    const lowered = event.text.toLowerCase();
    if (!lowered.includes("error") && !lowered.includes("fallback")) {
      return { state, line: null };
    }
  }
  if (event.kind === "tool_result") {
    const summary = summarizeEventForFeed(event.kind, event.text, event.payload).toLowerCase();
    if (summary === "tool result: finished") {
      return { state, line: null };
    }
  }

  return {
    state,
    line: `[${event.kind}] ${summarizeEventForFeed(event.kind, event.text, event.payload)}`,
  };
}
