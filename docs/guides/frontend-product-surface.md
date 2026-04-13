# Frontend Product Surface Guide

This guide documents the frontend architecture for the Fleet RLM workbench — the primary product surface for the Daytona-backed recursive DSPy runtime.

## Product Flow Overview

The supported product flow is a **chat-first execution workbench**:

1. **User submits a task** via the composer
2. **Backend creates a Daytona sandbox** and begins execution
3. **Streaming events** flow through the websocket showing progress
4. **Trajectory steps** display reasoning and tool calls
5. **Final result** appears as an assistant turn with summary

### Execution States

The workspace tracks execution through these states:

| State | Description | UI Treatment |
|-------|-------------|--------------|
| `idle` | Ready for input | Composer enabled, empty state shown |
| `bootstrapping` | Sandbox starting | Loading indicator, "Setting up workspace…" |
| `running` | Execution in progress | Streaming trajectory, progress indicators |
| `completed` | Task finished successfully | Final answer shown, composer ready |
| `error` | Execution failed | Error message with details |
| `needs_human_review` | HITL checkpoint | Review action buttons displayed |
| `cancelled` | User stopped execution | Neutral completion state |

## Component Architecture

### Layer Structure

```
src/
├── routes/           # File-based routing (TanStack Router)
├── features/         # Product surface entry points
│   ├── workspace/    # Main workbench surface
│   ├── volumes/      # Durable storage browser
│   ├── optimization/ # DSPy optimization UI
│   ├── settings/     # Configuration screens
│   └── layout/       # App chrome and shell
├── app/              # Feature implementation internals
│   └── workspace/    # Workspace UI components
│       ├── transcript/       # Message list and rendering
│       ├── assistant-content/ # Turn display components
│       ├── composer/         # Input area controls
│       ├── inspector/        # Side panel details
│       └── workbench/        # Execution trace display
├── components/       # Reusable UI primitives
│   ├── ui/          # shadcn/Base UI components
│   ├── ai-elements/ # AI-specific components (conversation, message)
│   └── patterns/    # App-owned composition patterns
└── lib/             # Business logic and integrations
    ├── workspace/   # State stores and event adapters
    └── rlm-api/     # REST and websocket clients
```

### Key Component Boundaries

**Feature entry points** (`features/*`) are thin wrappers that compose implementation from `app/*` and `lib/*`:

```tsx
// features/workspace/workspace-screen.tsx
export function WorkspaceScreen() {
  const runtime = useWorkspace(); // from lib/workspace
  return (
    <div>
      <WorkspaceMessageList {...} /> {/* from app/workspace */}
      <WorkspaceComposer {...} />
    </div>
  );
}
```

**Implementation components** (`app/workspace/*`) handle rendering and user interaction but don't own business logic:

```tsx
// app/workspace/transcript/workspace-message-list.tsx
export function WorkspaceMessageList({ messages, isTyping, ... }) {
  // Pure rendering logic, receives props from parent
}
```

**State and adapters** (`lib/workspace/*`) own backend event mapping and state management:

```tsx
// lib/workspace/chat-store.ts - Zustand store for chat state
// lib/workspace/backend-chat-event-adapter.ts - Maps WS frames to chat messages
```

## State Management

### Primary Stores

| Store | Purpose | Location |
|-------|---------|----------|
| `useChatStore` | Chat messages, streaming state | `lib/workspace/chat-store.ts` |
| `useWorkspaceUiStore` | UI state (inspector, selection) | `lib/workspace/workspace-ui-store.ts` |
| `useRunWorkbenchStore` | Execution state, iterations | `lib/workspace/run-workbench-store.ts` |
| `useArtifactStore` | Execution artifacts | `lib/workspace/artifact-store.ts` |
| `useChatHistoryStore` | Persisted conversations | `lib/workspace/chat-history-store.ts` |

### State Flow

```
WebSocket Frame
    ↓
applyWsFrameToMessages() — adapts frame to ChatMessage[]
    ↓
useChatStore.setMessages() — updates Zustand state
    ↓
WorkspaceMessageList — renders via React subscription
```

## Websocket Integration

### Connection Flow

1. `useWorkspace()` hook manages the connection lifecycle
2. `streamChatOverWs()` opens connection and sends initial message
3. `onFrame` callback receives streaming events
4. Events are adapted via `applyWsFrameToMessages()`

### Event Types

Events from the backend websocket (`WsEventKind`):

| Event | Purpose |
|-------|---------|
| `assistant_token` | Streaming text tokens |
| `reasoning_step` | Model reasoning display |
| `trajectory_step` | Execution trace step |
| `tool_call` / `tool_result` | Tool invocations |
| `status` | Progress updates |
| `final` | Execution completed |
| `error` | Execution failed |
| `hitl_request` | Human review needed |

### Adding Support for New Event Types

1. Add the event kind to `WsEventKind` in `lib/rlm-api/ws-types.ts`
2. Update `applyWsFrameToMessages()` in `lib/workspace/backend-chat-event-adapter.ts`
3. Add rendering in `app/workspace/transcript/trace-part-renderers.tsx`
4. Update display item building in `lib/workspace/chat-display-items.ts` if needed

## Component Patterns

### Status Components

Use the execution status pattern components for consistent state display:

```tsx
import { StatusBadge, StatusIndicator, StatusMessage } from "@/components/patterns/execution-status";

<StatusBadge status="running" />
<StatusIndicator status="completed" />
<StatusMessage variant="warning" title="Sandbox Starting">
  Your workspace is being prepared...
</StatusMessage>
```

### Section Layout

Use section layout components for consistent spacing and structure:

```tsx
import { Section, SectionHeader, SectionContent } from "@/components/patterns/section-layout";

<Section spacing="default">
  <SectionHeader title="Execution Trace" />
  <SectionContent scroll>
    {/* Scrollable content */}
  </SectionContent>
</Section>
```

### Empty States

The empty state pattern shows contextual guidance:

```tsx
import { EmptyPanel } from "@/components/patterns/empty-panel";

<EmptyPanel
  title="No results yet"
  description="Submit a task to see execution results"
  icon={FileSearch}
/>
```

## Styling Conventions

### Design Tokens

The frontend uses a layered token system:

1. **Tailwind v4 baseline** — Core utility classes
2. **shadcn/Base UI tokens** — Component-level tokens via CSS variables
3. **App semantic extensions** — Product-specific tokens in `globals.css`

Key tokens:

```css
/* Semantic colors */
--foreground          /* Primary text */
--muted-foreground    /* Secondary text */
--border              /* Default borders */
--border-subtle       /* Light borders */

/* Status colors */
--destructive         /* Error states */
--primary             /* Interactive elements */

/* Surfaces */
--background          /* Page background */
--card                /* Elevated surfaces */
--muted               /* Subtle backgrounds */
```

### Typography Utilities

Use semantic typography utilities:

```html
<h1 class="typo-h1">Page Title</h1>
<p class="typo-base">Body text</p>
<span class="typo-caption">Small label</span>
<code class="typo-mono">code</code>
```

### Layout Utilities

```html
<div class="max-w-content">Prose width content</div>
<div class="max-w-container">Container width content</div>
```

## Responsive Behavior

### Breakpoints

The workspace adapts at these breakpoints:

| Breakpoint | Behavior |
|------------|----------|
| Mobile (<768px) | Single column, bottom sheet canvas |
| Tablet (768-1024px) | Side-by-side with collapsed sidebar |
| Desktop (>1024px) | Full layout with expanded sidebar |

### Mobile Adaptations

- Composer moves to fixed bottom position
- Inspector panel becomes a bottom sheet
- Empty state copy adjusts for mobile
- Touch targets meet 44px minimum

## Testing

### Test Structure

Tests are colocated with their modules under `__tests__/`:

```
app/workspace/transcript/__tests__/
  workspace-message-list.ai-elements.test.tsx
  workspace-chat-empty-state.test.tsx
```

### Key Test Patterns

**Component rendering:**
```tsx
import { render, screen } from "@/test/test-utils";

test("renders empty state", () => {
  render(<WorkspaceChatEmptyState isMobile={false} onSuggestionClick={vi.fn()} />);
  expect(screen.getByText(/What would you like to build/)).toBeInTheDocument();
});
```

**State integration:**
```tsx
import { useChatStore } from "@/lib/workspace/chat-store";

test("applies message from frame", () => {
  const { result } = renderHook(() => useChatStore());
  // Test store interactions
});
```

## Out of Scope

The frontend intentionally does **not**:

- Implement recursive execution logic (belongs in `fleet_rlm.worker`)
- Make direct Daytona API calls (abstracted by backend)
- Store sensitive credentials (handled by backend)
- Derive execution decisions from UI heuristics (follows backend state)

The frontend is a **presentation and orchestration layer** for the backend-driven execution flow.

## Common Tasks

### Adding a new execution event visualization

1. Define the render part type in `workspace-types.ts`
2. Add frame→message mapping in `backend-chat-event-adapter.ts`
3. Add rendering component in `trace-part-renderers.tsx`
4. Update `buildChatDisplayItems()` if grouping logic changes
5. Add tests for the new rendering

### Updating empty state messaging

Edit `workspace-chat-empty-state.tsx`:
- Update suggestions array for new prompts
- Modify copy in the component JSX

### Adding a new status state

1. Add the status to `RunStatus` type in `workspace-types.ts`
2. Update `STATUS_ICONS` and `STATUS_LABELS` in `execution-status.tsx`
3. Add variant styles in the CVA definitions
4. Update any components that switch on status

## Related Documentation

- [Root AGENTS.md](../../AGENTS.md) — Repository-wide conventions
- [Frontend AGENTS.md](../../src/frontend/AGENTS.md) — Frontend-specific rules
- [Architecture Overview](../architecture.md) — System architecture
