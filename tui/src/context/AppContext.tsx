/**
 * Global application state management using React Context + useReducer.
 * Mirrors the Textual app's state architecture.
 */

import { createContext, useContext, useReducer, type ReactNode } from "react";
import type {
  StreamEvent,
  TurnState,
  TraceMode,
  TranscriptEvent,
  ConnectionState,
} from "../types/protocol";
import { createEmptyTurnState, applyStreamEvent } from "../types/state";

/** Application state */
export interface AppState {
  // Connection
  connectionState: ConnectionState;
  wsUrl: string;

  // Session config
  traceMode: TraceMode;
  streamEnabled: boolean;
  docsPath: string | null;

  // Current turn
  currentTurn: TurnState;
  isProcessing: boolean;

  // Transcript history
  transcript: TranscriptEvent[];

  // Command result
  lastCommandResult: {
    command: string;
    result: Record<string, unknown>;
  } | null;

  // UI state
  activeTab: "reasoning" | "tools" | "stats";
  reasoningVisible: boolean;
  toolsVisible: boolean;
  inputValue: string;
  statusMessage: string;
}

/** Action types */
export type AppAction =
  | { type: "SET_CONNECTION_STATE"; payload: ConnectionState }
  | { type: "SET_WS_URL"; payload: string }
  | { type: "SET_TRACE_MODE"; payload: TraceMode }
  | { type: "SET_STREAM_ENABLED"; payload: boolean }
  | { type: "SET_DOCS_PATH"; payload: string | null }
  | { type: "START_TURN" }
  | { type: "APPLY_EVENT"; payload: StreamEvent }
  | { type: "END_TURN" }
  | { type: "CANCEL_TURN" }
  | { type: "ADD_TRANSCRIPT"; payload: TranscriptEvent }
  | { type: "CLEAR_TRANSCRIPT" }
  | { type: "SET_ACTIVE_TAB"; payload: "reasoning" | "tools" | "stats" }
  | { type: "TOGGLE_REASONING" }
  | { type: "TOGGLE_TOOLS" }
  | { type: "SET_INPUT_VALUE"; payload: string }
  | { type: "SET_STATUS_MESSAGE"; payload: string }
  | { type: "RESET_TURN" }
  | {
      type: "APPLY_COMMAND_RESULT";
      payload: { command: string; result: Record<string, unknown> };
    };

/** Initial state */
const initialState: AppState = {
  connectionState: "disconnected",
  wsUrl: getDefaultWsUrl(),
  traceMode: "compact",
  streamEnabled: true,
  docsPath: null,
  currentTurn: createEmptyTurnState(),
  isProcessing: false,
  transcript: [],
  lastCommandResult: null,
  activeTab: "reasoning",
  reasoningVisible: true,
  toolsVisible: true,
  inputValue: "",
  statusMessage: "Disconnected",
};

/** Reducer function */
function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "SET_CONNECTION_STATE":
      return {
        ...state,
        connectionState: action.payload,
        statusMessage:
          action.payload === "connected"
            ? "Connected"
            : action.payload === "connecting"
              ? "Connecting..."
              : action.payload === "error"
                ? "Connection error"
                : "Disconnected",
      };

    case "SET_WS_URL":
      return { ...state, wsUrl: action.payload };

    case "SET_TRACE_MODE":
      return { ...state, traceMode: action.payload };

    case "SET_STREAM_ENABLED":
      return { ...state, streamEnabled: action.payload };

    case "SET_DOCS_PATH":
      return { ...state, docsPath: action.payload };

    case "START_TURN":
      return {
        ...state,
        isProcessing: true,
        currentTurn: createEmptyTurnState(),
        statusMessage: "Processing...",
      };

    case "APPLY_EVENT":
      return {
        ...state,
        currentTurn: applyStreamEvent(state.currentTurn, action.payload),
      };

    case "END_TURN": {
      // Add final turn to transcript
      const transcriptEvent: TranscriptEvent = {
        role: "assistant",
        content: state.currentTurn.finalText || state.currentTurn.transcriptText,
        payload: state.currentTurn.trajectory,
      };

      return {
        ...state,
        isProcessing: false,
        transcript: [...state.transcript, transcriptEvent],
        statusMessage: state.currentTurn.errored
          ? "Error occurred"
          : state.currentTurn.cancelled
            ? "Cancelled"
            : "Ready",
      };
    }

    case "CANCEL_TURN":
      return {
        ...state,
        isProcessing: false,
        statusMessage: "Cancelled",
      };

    case "RESET_TURN":
      return {
        ...state,
        currentTurn: createEmptyTurnState(),
        isProcessing: false,
      };

    case "ADD_TRANSCRIPT":
      return {
        ...state,
        transcript: [...state.transcript, action.payload],
      };

    case "CLEAR_TRANSCRIPT":
      return {
        ...state,
        transcript: [],
        currentTurn: createEmptyTurnState(),
      };

    case "SET_ACTIVE_TAB":
      return { ...state, activeTab: action.payload };

    case "TOGGLE_REASONING":
      return { ...state, reasoningVisible: !state.reasoningVisible };

    case "TOGGLE_TOOLS":
      return { ...state, toolsVisible: !state.toolsVisible };

    case "SET_INPUT_VALUE":
      return { ...state, inputValue: action.payload };

    case "SET_STATUS_MESSAGE":
      return { ...state, statusMessage: action.payload };

    case "APPLY_COMMAND_RESULT": {
      const { command, result } = action.payload;
      const isError = result.status === "error";
      const message = isError
        ? `Command ${command} failed: ${result.error ?? "unknown error"}`
        : formatCommandResult(command, result);

      return {
        ...state,
        lastCommandResult: action.payload,
        transcript: [
          ...state.transcript,
          {
            role: "system" as const,
            content: message,
            payload: result,
          },
        ],
      };
    }

    default:
      return state;
  }
}

/** Format a command result for display in transcript */
function formatCommandResult(
  command: string,
  result: Record<string, unknown>,
): string {
  switch (command) {
    case "load_document":
      return `Loaded document "${result.alias}" from ${result.path} (${result.chars} chars, ${result.lines} lines)`;
    case "set_active_document":
      return `Active document set to: ${result.active_alias}`;
    case "list_documents":
      return `Documents: ${JSON.stringify(result.documents, null, 2)}\nActive: ${result.active_alias}`;
    case "chunk_host":
      return `Chunked with "${result.strategy}": ${result.chunk_count} chunks\nPreview: ${String(result.preview ?? "").slice(0, 200)}`;
    case "chunk_sandbox":
      return `Sandbox chunked with "${result.strategy}": ${result.chunk_count} chunks -> buffer "${result.buffer_name}"`;
    case "parallel_semantic_map":
      return `Semantic map: ${result.findings_count} findings from ${result.chunk_count} chunks -> buffer "${result.buffer_name}"`;
    case "analyze_document":
      return `Analysis complete (${result.doc_chars} chars examined)\nAnswer: ${result.answer}`;
    case "summarize_document":
      return `Summary (${result.coverage_pct}% coverage, ${result.doc_chars} chars):\n${result.summary}`;
    case "extract_logs":
      return `Extracted ${(result.matches as unknown[])?.length ?? 0} matches\nPatterns: ${JSON.stringify(result.patterns)}`;
    case "read_buffer":
      return `Buffer "${result.name}": ${result.count} items\n${JSON.stringify(result.items, null, 2).slice(0, 500)}`;
    case "clear_buffer":
      return `Buffer cleared (scope: ${result.scope})`;
    case "save_buffer":
      return `Buffer saved to volume: ${result.saved_path} (${result.item_count} items)`;
    case "load_volume":
      return `Loaded from volume as "${result.alias}" (${result.chars} chars, ${result.lines} lines)`;
    case "reset":
      return `Session reset. History: ${result.history_turns} turns, buffers cleared: ${result.buffers_cleared}`;
    default:
      return `${command}: ${JSON.stringify(result, null, 2)}`;
  }
}

/** Context type */
interface AppContextType {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

/** Provider component */
export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

/** Hook to use app context */
export function useAppContext() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useAppContext must be used within AppProvider");
  }
  return context;
}

/**
 * Helper function to get the WebSocket URL.
 * Uses Bun.env?.WS_URL if available, otherwise defaults to "ws://localhost:8000/ws/chat".
 */
function getDefaultWsUrl(): string {
  if (typeof Bun !== "undefined" && Bun.env?.WS_URL) {
    return Bun.env.WS_URL;
  }
  return "ws://localhost:8000/ws/chat";
}
