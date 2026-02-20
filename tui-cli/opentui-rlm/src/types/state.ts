/**
 * State management utilities for TurnState.
 * Mirrors the Python TurnState.apply() method.
 */

import type { StreamEvent, TurnState } from "./protocol";

/** Create an empty TurnState */
export function createEmptyTurnState(): TurnState {
  return {
    assistantTokens: [],
    transcriptText: "",
    reasoningLines: [],
    toolTimeline: [],
    statusLines: [],
    streamChunks: [],
    thoughtChunks: [],
    statusMessages: [],
    trajectory: {},
    finalText: "",
    historyTurns: 0,
    tokenCount: 0,
    cancelled: false,
    errored: false,
    done: false,
    errorMessage: "",
  };
}

/** Apply a stream event to a TurnState (immutable) */
export function applyStreamEvent(
  state: TurnState,
  event: StreamEvent
): TurnState {
  // Clone state for immutability
  const newState = { ...state };

  switch (event.kind) {
    case "assistant_token": {
      const token = event.text;
      newState.assistantTokens = [...state.assistantTokens, token];
      newState.streamChunks = [...state.streamChunks, token];
      newState.tokenCount = state.tokenCount + 1;
      newState.transcriptText = newState.assistantTokens.join("");
      break;
    }

    case "status": {
      if (event.text) {
        newState.statusLines = [...state.statusLines, event.text];
        newState.statusMessages = [...state.statusMessages, event.text];
        newState.reasoningLines = [...state.reasoningLines, event.text];
      }
      break;
    }

    case "reasoning_step": {
      if (event.text) {
        newState.reasoningLines = [...state.reasoningLines, event.text];
        newState.thoughtChunks = [...state.thoughtChunks, event.text];
      }
      break;
    }

    case "tool_call": {
      if (event.text) {
        newState.toolTimeline = [...state.toolTimeline, event.text];
      }
      break;
    }

    case "tool_result": {
      if (event.text) {
        newState.toolTimeline = [...state.toolTimeline, event.text];
      }
      break;
    }

    case "final": {
      const finalText = event.text || state.transcriptText;
      newState.finalText = finalText;
      newState.transcriptText = finalText;
      newState.trajectory = { ...(event.payload.trajectory as Record<string, unknown> || {}) };
      newState.historyTurns = Number(event.payload.history_turns ?? state.historyTurns);
      newState.done = true;
      break;
    }

    case "cancelled": {
      newState.cancelled = true;
      newState.done = true;
      const cancelledText = event.text || state.transcriptText;
      newState.finalText = cancelledText;
      newState.transcriptText = cancelledText;
      newState.historyTurns = Number(event.payload.history_turns ?? state.historyTurns);
      break;
    }

    case "error": {
      newState.errored = true;
      newState.done = true;
      newState.errorMessage = event.text || "unknown error";
      newState.historyTurns = Number(event.payload.history_turns ?? state.historyTurns);
      break;
    }
  }

  return newState;
}

/** Apply multiple stream events to a TurnState */
export function applyStreamEvents(
  state: TurnState,
  events: StreamEvent[]
): TurnState {
  return events.reduce((acc, event) => applyStreamEvent(acc, event), state);
}
