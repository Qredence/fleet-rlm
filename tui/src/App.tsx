/**
 * Main App component - layout shell for the fleet-rlm TUI.
 * Layout: StatusBar + main split (ChatPane + TabPanel) + InputBar + HintsBar
 * Polished with base background and proper spacing.
 */

import { useCallback } from "react";
import { AppProvider, useAppContext } from "./context/AppContext";
import { useWebSocket } from "./hooks/useWebSocket";
import { StatusBar } from "./components/StatusBar";
import { HintsBar } from "./components/HintsBar";
import { ChatPane } from "./components/ChatPane";
import { TabPanel } from "./components/TabPanel";
import { InputBar } from "./components/InputBar";
import { useKeyboard, useRenderer } from "@opentui/react";
import { bg } from "./theme";

function AppContent() {
  const { state, dispatch } = useAppContext();
  const renderer = useRenderer();

  // WebSocket connection
  const ws = useWebSocket({
    url: state.wsUrl,
    autoConnect: true,
    workspaceId: state.workspaceId,
    userId: state.userId,
    sessionId: state.sessionId,
    onEvent: (event) => {
      dispatch({ type: "APPLY_EVENT", payload: event });

      // End turn on final/error/cancelled
      if (event.kind === "final" || event.kind === "error" || event.kind === "cancelled") {
        dispatch({ type: "END_TURN" });
      }
    },
    onError: (error) => {
      dispatch({ type: "SET_STATUS_MESSAGE", payload: `Error: ${error}` });

      // Show helpful message for connection errors
      if (error.includes("WebSocket") || error.includes("Connection")) {
        dispatch({
          type: "ADD_TRANSCRIPT",
          payload: {
            role: "system",
            content: "Cannot connect to backend server.\n\nStart the server with:\n  uv run fleet-rlm serve-api",
          },
        });
      }
    },
    onConnectionChange: (connectionState) => {
      dispatch({ type: "SET_CONNECTION_STATE", payload: connectionState });
    },
    onCommandResult: (command, result) => {
      dispatch({
        type: "APPLY_COMMAND_RESULT",
        payload: { command, result },
      });
    },
  });

  // Handle message submission
  const handleSubmit = useCallback(
    (text: string) => {
      if (state.connectionState !== "connected") {
        return;
      }

      // Add user message to transcript
      dispatch({
        type: "ADD_TRANSCRIPT",
        payload: { role: "user", content: text },
      });

      // Start turn
      dispatch({ type: "START_TURN" });

      // Send to backend
      ws.sendMessage(text, state.docsPath ?? undefined, state.traceMode);
    },
    [state.connectionState, state.docsPath, state.traceMode, ws, dispatch]
  );

  // Handle slash commands
  const handleSlashCommand = useCallback(
    (command: string, args: string) => {
      switch (command) {
        case "/exit":
        case "/quit":
          renderer.destroy();
          break;

        case "/help":
          dispatch({
            type: "ADD_TRANSCRIPT",
            payload: {
              role: "system",
              content: `Available commands:

Session:
  /exit, /quit        Exit the application
  /clear              Clear chat history
  /trace [mode]       Set trace mode (compact, verbose, off)
  /reset              Reset agent session (history + buffers)
  /help               Show this help

Documents:
  /docs <path>        Load a document (set as active)
  /load <path> [alias]  Load document with optional alias
  /active <alias>     Set active document alias
  /list               List loaded documents

Processing:
  /chunk <strategy> [size]   Chunk active doc (size|headers|timestamps|json)
  /analyze <query>    Analyze active document with RLM
  /summarize <focus>  Summarize active document with RLM
  /extract <query>    Extract patterns from logs with RLM
  /semantic <query>   Parallel semantic map over chunks

Buffers & Storage:
  /buffer <name>      Read sandbox buffer contents
  /clear-buffer [name]  Clear one buffer (or all)
  /save-buffer <name> <path>  Save buffer to Modal volume
  /load-volume <path> [alias]  Load text from Modal volume`,
            },
          });
          break;

        case "/clear":
          dispatch({ type: "CLEAR_TRANSCRIPT" });
          break;

        case "/trace":
          if (args === "compact" || args === "verbose" || args === "off") {
            dispatch({ type: "SET_TRACE_MODE", payload: args });
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: {
                role: "system",
                content: `Trace mode set to: ${args}`,
              },
            });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: {
                role: "system",
                content: "Usage: /trace <compact|verbose|off>",
              },
            });
          }
          break;

        // --- Document commands ---

        case "/docs":
          if (args) {
            // /docs sends both a local state update AND a backend command
            dispatch({ type: "SET_DOCS_PATH", payload: args });
            ws.sendCommand("load_document", { path: args });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /docs <path>" },
            });
          }
          break;

        case "/load": {
          const loadParts = args.split(/\s+/, 2);
          const loadPath = loadParts[0];
          const loadAlias = loadParts[1] || undefined;
          if (!loadPath) {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /load <path> [alias]" },
            });
            break;
          }
          const loadArgs: Record<string, unknown> = { path: loadPath };
          if (loadAlias) loadArgs.alias = loadAlias;
          ws.sendCommand("load_document", loadArgs);
          break;
        }

        case "/active":
          if (args) {
            ws.sendCommand("set_active_document", { alias: args });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /active <alias>" },
            });
          }
          break;

        case "/list":
          ws.sendCommand("list_documents", {});
          break;

        // --- Processing commands ---

        case "/chunk": {
          const chunkParts = args.split(/\s+/, 2);
          const strategy = chunkParts[0];
          const chunkSize = chunkParts[1] ? parseInt(chunkParts[1], 10) : undefined;
          if (!strategy) {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: {
                role: "system",
                content: "Usage: /chunk <size|headers|timestamps|json> [chunk_size]",
              },
            });
            break;
          }
          const chunkArgs: Record<string, unknown> = { strategy };
          if (chunkSize && !isNaN(chunkSize)) chunkArgs.size = chunkSize;
          ws.sendCommand("chunk_host", chunkArgs);
          break;
        }

        case "/analyze":
          if (args) {
            ws.sendCommand("analyze_document", { query: args });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /analyze <query>" },
            });
          }
          break;

        case "/summarize":
          if (args) {
            ws.sendCommand("summarize_document", { focus: args });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /summarize <focus>" },
            });
          }
          break;

        case "/extract":
          if (args) {
            ws.sendCommand("extract_logs", { query: args });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /extract <query>" },
            });
          }
          break;

        case "/semantic": {
          if (!args) {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: {
                role: "system",
                content: "Usage: /semantic <query> [chunk_strategy] [max_chunks]",
              },
            });
            break;
          }
          // Parse: query is everything before optional --strategy/--max flags
          // Simple: first arg group is query, optional 2nd is strategy, 3rd is max_chunks
          const semParts = args.split(/\s+/);
          // Find if there are strategy/max arguments at the end
          let semQuery = args;
          const semArgs: Record<string, unknown> = {};
          // Look for last two tokens as possible strategy and max_chunks
          if (semParts.length >= 3) {
            const lastToken = semParts[semParts.length - 1] ?? "";
            const secondLast = semParts[semParts.length - 2] ?? "";
            const maybeMax = parseInt(lastToken, 10);
            const maybeStrategy = secondLast;
            if (
              !isNaN(maybeMax) &&
              ["size", "headers", "timestamps", "json"].includes(maybeStrategy)
            ) {
              semQuery = semParts.slice(0, -2).join(" ");
              semArgs.chunk_strategy = maybeStrategy;
              semArgs.max_chunks = maybeMax;
            }
          }
          semArgs.query = semQuery;
          ws.sendCommand("parallel_semantic_map", semArgs);
          break;
        }

        // --- Buffer & storage commands ---

        case "/buffer":
          if (args) {
            ws.sendCommand("read_buffer", { name: args });
          } else {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: { role: "system", content: "Usage: /buffer <name>" },
            });
          }
          break;

        case "/clear-buffer":
          ws.sendCommand("clear_buffer", args ? { name: args } : {});
          break;

        case "/save-buffer": {
          const saveParts = args.split(/\s+/, 2);
          if (saveParts.length < 2 || !saveParts[0] || !saveParts[1]) {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: {
                role: "system",
                content: "Usage: /save-buffer <buffer_name> <volume_path>",
              },
            });
            break;
          }
          ws.sendCommand("save_buffer", {
            name: saveParts[0],
            path: saveParts[1],
          });
          break;
        }

        case "/load-volume": {
          const volParts = args.split(/\s+/, 2);
          const volPath = volParts[0];
          const volAlias = volParts[1] || undefined;
          if (!volPath) {
            dispatch({
              type: "ADD_TRANSCRIPT",
              payload: {
                role: "system",
                content: "Usage: /load-volume <path> [alias]",
              },
            });
            break;
          }
          const volArgs: Record<string, unknown> = { path: volPath };
          if (volAlias) volArgs.alias = volAlias;
          ws.sendCommand("load_volume", volArgs);
          break;
        }

        case "/reset":
          ws.sendCommand("reset", {});
          dispatch({ type: "CLEAR_TRANSCRIPT" });
          break;

        default:
          dispatch({
            type: "ADD_TRANSCRIPT",
            payload: {
              role: "system",
              content: `Unknown command: ${command}. Type /help for available commands.`,
            },
          });
      }
    },
    [renderer, dispatch, ws]
  );

  // Keyboard shortcuts
  useKeyboard((key) => {
    if (key.ctrl && key.name === "c") {
      if (state.isProcessing) {
        ws.sendCancel();
        dispatch({ type: "CANCEL_TURN" });
      }
    }

    if (key.ctrl && key.name === "l") {
      dispatch({ type: "CLEAR_TRANSCRIPT" });
    }

    if (key.name === "f2") {
      dispatch({ type: "TOGGLE_REASONING" });
    }

    if (key.name === "f3") {
      dispatch({ type: "TOGGLE_TOOLS" });
    }
  });

  return (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      backgroundColor={bg.base}
    >
      {/* Top status bar */}
      <StatusBar />

      {/* Main content area */}
      <box flexGrow={1} flexDirection="row" paddingLeft={1} paddingRight={1} gap={1}>
        {/* Left: Chat transcript (70%) */}
        <box flexGrow={7}>
          <ChatPane />
        </box>

        {/* Right: Tabbed panel (30%) */}
        <box flexGrow={3}>
          <TabPanel />
        </box>
      </box>

      {/* Input bar */}
      <InputBar onSubmit={handleSubmit} onSlashCommand={handleSlashCommand} />

      {/* Bottom hints bar */}
      <HintsBar />
    </box>
  );
}

export function App() {
  return (
    <AppProvider>
      <AppContent />
    </AppProvider>
  );
}
