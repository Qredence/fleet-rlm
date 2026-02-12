# fleet-rlm OpenTUI Frontend

OpenTUI React frontend for the `fleet-rlm code-chat` interactive session.

## Overview

This is a standalone Bun/TypeScript package that provides a modern terminal UI using OpenTUI React. It communicates with the Python backend via WebSocket at `ws://localhost:8000/ws/chat`.

## Architecture

```
┌─────────────────────────────────────────┐
│ App.tsx (Layout + WebSocket)            │
│  ├─ StatusBar (connection, status)      │
│  ├─ Main Split                           │
│  │   ├─ ChatPane (70%)                  │
│  │   └─ TabPanel (30%)                  │
│  │       ├─ Reasoning                   │
│  │       ├─ Tools                        │
│  │       └─ Stats                        │
│  ├─ InputBar (user input)               │
│  └─ HintsBar (keyboard shortcuts)       │
└─────────────────────────────────────────┘
         │
         ▼ WebSocket (ws://)
┌─────────────────────────────────────────┐
│ FastAPI Backend (/ws/chat)               │
│  └─ RLMReActChatAgent                    │
│      └─ ModalInterpreter                 │
└─────────────────────────────────────────┘
```

## Key Files

- **`src/App.tsx`** - Root component with layout + WebSocket integration + keyboard shortcuts
- **`src/context/AppContext.tsx`** - Global state management with useReducer
- **`src/hooks/useWebSocket.ts`** - WebSocket connection management with auto-reconnect
- **`src/types/protocol.ts`** - TypeScript types mirroring Python protocol models
- **`src/types/state.ts`** - TurnState utilities and immutable reducers
- **`src/components/`** - UI components (StatusBar, ChatPane, TabPanel, InputBar, HintsBar)

## State Management

The app uses React's `useReducer` for global state:

- **Connection state**: connecting, connected, disconnected, error
- **Current turn**: live streaming updates (text, reasoning, tools)
- **Transcript history**: completed messages by role
- **Session config**: docs path, trace mode, token counts
- **UI state**: active tab, input value, processing flag

## WebSocket Protocol

**Client → Server:**
```json
{
  "type": "message",
  "content": "user message",
  "docs_path": "/path/to/doc.txt",
  "trace": true,
  "trace_mode": "compact"
}
```

The `trace_mode` can be `"compact"`, `"verbose"`, or `"off"`.

**Server → Client:**
```json
{
  "type": "event",
  "data": {
    "kind": "token" | "reasoning" | "tool_start" | "tool_end" | "final" | "error" | "cancelled",
    "text": "...",
    "tool_name": "...",
    "tool_input": "...",
    "tool_output": "...",
    "final_output": "...",
    "timestamp": 1234567890.123
  }
}
```

## Slash Commands

- `/exit`, `/quit` - Exit the application
- `/help` - Show help message
- `/clear` - Clear transcript
- `/trace [mode]` - Set trace mode (compact/verbose/off)
- `/docs <path>` - Load document as active context

## Keyboard Shortcuts

- **Ctrl+C** - Cancel current turn (if processing)
- **Ctrl+L** - Clear transcript
- **F2** - Toggle reasoning pane
- **F3** - Toggle tools pane

## Development

```bash
# Install dependencies
bun install

# Run in watch mode
bun run dev

# Start (single run)
bun run start

# Type check
bun run type-check
```

## Usage

Launch via the `fleet-rlm` CLI:

```bash
# OpenTUI React frontend
uv run fleet-rlm code-chat --opentui

# With document preloaded
uv run fleet-rlm code-chat --opentui --docs-path /path/to/doc.txt
```

## Prerequisites

Before running the OpenTUI frontend, you need to start the backend server:

```bash
# Terminal 1: Start the FastAPI server
uv run fleet-rlm serve-api

# Terminal 2: Run the OpenTUI frontend
uv run fleet-rlm code-chat --opentui
```

The CLI will automatically check if the server is running and provide helpful instructions if it's not.

### Environment Variables

- `WS_URL` - WebSocket endpoint URL (default: `ws://localhost:8000/ws/chat`)

  Example:
  ```bash
  WS_URL=ws://localhost:8000/ws/chat uv run fleet-rlm code-chat --opentui
  ```

---

This project was created using `bun create tui`. [create-tui](https://git.new/create-tui) is the easiest way to get started with OpenTUI.
