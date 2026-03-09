# Frontend Development

This guide covers the frontend development workflow for Fleet-RLM. The frontend is a React + TypeScript application built with Vite, located in `src/frontend/`.

## Quick Start

```bash
# From repository root
cd src/frontend

# Install dependencies
bun install --frozen-lockfile

# Start development server
bun run dev
```

The development server runs at `http://localhost:5173` with hot module replacement. It proxies API requests to the backend at `http://localhost:8000`.

## Development Commands

All commands run from `src/frontend/`:

| Command | Description |
|---------|-------------|
| `bun run dev` | Start development server with HMR |
| `bun run build` | Production build |
| `bun run preview` | Preview production build locally |
| `bun run type-check` | TypeScript type checking |
| `bun run lint` | ESLint code linting |
| `bun run format` | Format code with Prettier |
| `bun run format:check` | Check formatting without changes |
| `bun run test:unit` | Run Vitest unit tests |
| `bun run test:watch` | Run Vitest in watch mode |
| `bun run test:coverage` | Run Vitest with coverage report |
| `bun run test:e2e` | Run Playwright end-to-end tests |
| `bun run check` | Full quality gate (type-check + lint + test + build + e2e) |

### Quality Gate

The `bun run check` command runs the complete validation suite:

1. TypeScript type checking
2. ESLint linting
3. Vitest unit tests
4. Production build
5. Playwright E2E tests

Run this before submitting a PR to catch issues early.

## Project Structure

```text
src/frontend/src/
├── app/                    # Application shell and routing
│   ├── layout/             # Layout components (DesktopShell, MobileShell)
│   ├── pages/              # Route-level page components
│   ├── providers/          # React context providers
│   └── routes.ts           # Route definitions
├── components/             # Shared UI components
│   ├── ai-elements/        # AI chat interface components
│   ├── chat/               # Chat input components
│   ├── domain/             # Domain-specific components
│   ├── shared/             # Reusable UI primitives
│   └── ui/                 # shadcn/ui components
├── features/               # Feature-based modules
│   ├── artifacts/          # Code artifact display
│   ├── rlm-workspace/      # Main chat workspace
│   ├── settings/           # Settings dialog and panes
│   ├── shell/              # Shell components (CommandPalette, UserMenu)
│   └── volumes/            # Volume browser
├── hooks/                  # Custom React hooks
├── lib/                    # Utilities and API clients
│   ├── auth/               # Authentication utilities
│   ├── config/             # Configuration
│   ├── data/               # Data types and mock data
│   ├── perf/               # Performance utilities
│   ├── rlm-api/            # Backend API client
│   ├── telemetry/          # Analytics/telemetry
│   └── utils/              # General utilities
├── stores/                 # Zustand state stores
├── styles/                 # Global styles
└── test/                   # Test setup and utilities
```

## Component Organization

### Features (`features/`)

Features are self-contained modules with their own components, hooks, and sometimes state. Each feature represents a distinct product area.

| Feature | Purpose |
|---------|---------|
| `rlm-workspace/` | Main chat interface for DSPy.RLM runtime |
| `settings/` | Runtime model configuration dialogs |
| `shell/` | Application shell components (CommandPalette, UserMenu) |
| `artifacts/` | Code and file artifact display |
| `volumes/` | Modal volume browser |

**Guidelines:**

- New product features belong in `features/`
- Features should be self-contained with minimal cross-feature dependencies
- Each feature can have its own `__tests__/` subdirectory for unit tests

### Shared UI Components (`components/`)

#### `components/ui/` — shadcn/ui Primitives

Radix UI primitives styled with Tailwind CSS. These are the building blocks for all UI.

Key components:
- `button.tsx`, `icon-button.tsx` — Button variants
- `dialog.tsx`, `drawer.tsx`, `sheet.tsx` — Modal surfaces
- `sidebar.tsx` — Sidebar navigation
- `tabs.tsx`, `animated-tabs.tsx` — Tab navigation
- `input.tsx`, `textarea.tsx` — Form inputs
- `select.tsx`, `command.tsx` — Select and command menus
- `scroll-area.tsx` — Scrollable containers

**Guidelines:**

- Keep `components/ui/` limited to primitives and thin wrappers
- Do not add application-specific logic to UI primitives
- Use `buttonVariants()` and similar variant functions for consistent styling

#### `components/ai-elements/` — AI Chat Components

Specialized UI for chat applications from the ai-elements library:

| Component | Purpose |
|-----------|---------|
| `prompt-input.tsx` | Rich text input with attachment support |
| `message.tsx` | Chat message container |
| `reasoning.tsx` | Chain-of-thought display |
| `tool.tsx` | Tool call display |
| `code-block.tsx` | Syntax-highlighted code blocks |

#### `components/shared/` — Application Shared Components

Application-specific shared components that aren't generic primitives:

| Component | Purpose |
|-----------|---------|
| `ErrorBoundary.tsx` | React error boundary with fallback UI |
| `BrandMark.tsx` | Logo/brand mark |
| `LargeTitleHeader.tsx` | Page header component |
| `SkillMarkdown.tsx` | Markdown renderer for documentation |

### When to Use Which Directory

| Component Type | Location |
|----------------|----------|
| Generic UI primitive (button, input, dialog) | `components/ui/` |
| AI/chat-specific primitive | `components/ai-elements/` |
| Reusable across multiple features | `components/shared/` |
| Single-feature use case | `features/<feature>/` |
| Domain-specific (artifacts, files) | `components/domain/` |

## TypeScript Conventions

### Configuration

TypeScript is configured in `tsconfig.app.json` with strict mode enabled:

```json
{
  "compilerOptions": {
    "target": "ES2023",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true,
    "jsx": "react-jsx",
    "paths": {
      "@/*": ["./src/*"]
    }
  }
}
```

### Path Aliases

Use the `@/*` alias for imports:

```typescript
// ✅ Preferred
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";

// ❌ Avoid relative paths for deep imports
import { Button } from "../../../components/ui/button";
```

### Type Safety Features

- **Strict mode**: All strict type checking enabled
- **No unused variables**: Compiler warns on unused locals and parameters
- **Unchecked indexed access**: Array/object access returns `T | undefined`
- **No fallthrough**: Switch statement fallthrough is an error

### Component Typing

Use `React.forwardRef` for components that should forward refs:

```typescript
interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    ButtonVariantProps {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    // Implementation
  }
);
Button.displayName = "Button";
```

Use `React.ComponentProps` for extracting props from existing components:

```typescript
type DialogProps = React.ComponentProps<typeof Dialog>;
```

## State Management

### Zustand Stores

Global state uses Zustand stores located in `stores/`:

| Store | Purpose |
|-------|---------|
| `chatStore.ts` | Active conversation state and streaming |
| `navigationStore.ts` | Navigation, canvas, inspector state |
| `chatHistoryStore.ts` | LocalStorage-backed conversation history |
| `themeStore.ts` | Dark mode toggle |
| `artifactStore.ts` | Execution step tracking |

**Guidelines:**

- Use Zustand for ephemeral client state (UI state, streaming state)
- Use TanStack Query for backend-backed server state
- Keep stores focused and composable

### React Hooks

Custom hooks in `hooks/` provide reusable stateful logic:

| Hook | Purpose |
|------|---------|
| `useAuth.ts` | Authentication state and actions |
| `useAppNavigate.ts` | Type-safe navigation |
| `useCodeMirror.ts` | CodeMirror editor setup |
| `useFilesystem.ts` | File system operations |
| `useIsMobile.ts` | Responsive mobile detection |
| `useStickToBottom.ts` | Auto-scroll for chat lists |

## Testing

### Unit Tests (Vitest)

Vitest runs in jsdom environment for browser API simulation.

**Test Location:**

Tests are colocated with source files in `__tests__/` subdirectories:

```text
src/
├── features/rlm-workspace/__tests__/
│   ├── ChatMessageList.ai-elements.test.tsx
│   ├── backendChatEventAdapter.test.ts
│   └── chatDisplayItems.test.ts
├── hooks/__tests__/
│   └── useAppNavigate.test.ts
└── stores/__tests__/
    └── chatHistoryStore.test.ts
```

**Running Tests:**

```bash
# Run all unit tests
bun run test:unit

# Watch mode for development
bun run test:watch

# With coverage
bun run test:coverage
```

**Test Setup:**

Global setup in `src/test/setup.ts` provides:
- Web Storage mock (localStorage, sessionStorage)
- `window.matchMedia` mock
- `ResizeObserver` mock
- `IntersectionObserver` mock

**Coverage Thresholds:**

| Metric | Threshold |
|--------|-----------|
| Lines | 60% |
| Functions | 60% |
| Branches | 50% |
| Statements | 60% |

**Writing Tests:**

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/button";

describe("Button", () => {
  it("renders with text", () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole("button")).toHaveTextContent("Click me");
  });
});
```

### End-to-End Tests (Playwright)

Playwright runs against a production build for full browser testing.

**Test Location:**

E2E tests are in `tests/e2e/` at the frontend root.

**Running Tests:**

```bash
# Run E2E tests
bun run test:e2e
```

**Configuration:**

Playwright configuration in `playwright.config.ts`:
- Runs against `http://127.0.0.1:4173`
- Uses Chromium browser
- Retries twice on CI
- Captures traces, screenshots, and videos on failure

### Test Anti-Patterns

- **Don't** duplicate mock setup across test files — use `setup.ts`
- **Don't** import test utilities from other test files — keep tests isolated
- **Don't** skip tests without documenting why

## API Integration

### Backend API Client

The backend API client is in `lib/rlm-api/`:

```typescript
import { apiClient } from "@/lib/rlm-api/client";
import { wsClient } from "@/lib/rlm-api/wsClient";

// HTTP requests
const settings = await apiClient.get("/api/v1/runtime/settings");

// WebSocket streaming
wsClient.connect("/api/v1/ws/chat", {
  onMessage: (frame) => { /* handle frame */ },
  onError: (error) => { /* handle error */ }
});
```

### OpenAPI Sync

Keep TypeScript types in sync with the backend OpenAPI spec:

```bash
# Sync spec snapshot from backend
bun run api:sync-spec

# Generate TypeScript types
bun run api:types

# Full sync
bun run api:sync

# Check for drift (CI gate)
bun run api:check
```

Generated types are in `src/lib/rlm-api/generated/openapi.ts`. Do not edit this file manually.

## Styling

### Tailwind CSS

The project uses Tailwind CSS v4 with the Vite plugin. Global styles are in `styles/`.

### Class Name Utility

Use the `cn()` utility for conditional class names:

```typescript
import { cn } from "@/lib/utils/cn";

<div className={cn(
  "base-class",
  condition && "conditional-class",
  className
)} />
```

### Component Variants

Use `class-variance-authority` (CVA) for component variants:

```typescript
import { cva, type VariantProps } from "class-variance-authority";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-md",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground",
        outline: "border border-input bg-background",
        ghost: "hover:bg-accent",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

type ButtonVariantProps = VariantProps<typeof buttonVariants>;
```

## Environment Variables

Configure in `.env` (see `.env.example`):

| Variable | Purpose |
|----------|---------|
| `VITE_FLEET_API_URL` | Backend API base URL |
| `VITE_FLEET_WS_URL` | WebSocket URL |
| `VITE_FLEET_WORKSPACE_ID` | Workspace identifier |
| `VITE_FLEET_USER_ID` | User identifier |
| `VITE_FLEET_TRACE` | Enable tracing |
| `VITE_ENTRA_CLIENT_ID` | Microsoft Entra client ID |
| `VITE_ENTRA_AUTHORITY` | Microsoft Entra authority URL |
| `VITE_ENTRA_SCOPES` | OAuth scopes |
| `VITE_ENTRA_REDIRECT_PATH` | OAuth redirect path |

## Common Workflows

### Adding a New Component

1. Determine the correct location:
   - Generic primitive → `components/ui/`
   - Feature-specific → `features/<feature>/`
   - Shared across features → `components/shared/`

2. Create the component with TypeScript types:
   ```typescript
   // MyComponent.tsx
   import * as React from "react";
   import { cn } from "@/lib/utils/cn";

   interface MyComponentProps {
     className?: string;
   }

   export function MyComponent({ className }: MyComponentProps) {
     return <div className={cn("base", className)} />;
   }
   ```

3. Add tests in `__tests__/MyComponent.test.tsx`

4. Export from the appropriate barrel file if needed

### Adding a New Store

1. Create `stores/myStore.ts`:
   ```typescript
   import { create } from "zustand";

   interface MyState {
     value: string;
     setValue: (value: string) => void;
   }

   export const useMyStore = create<MyState>((set) => ({
     value: "",
     setValue: (value) => set({ value }),
   }));
   ```

2. Add tests in `stores/__tests__/myStore.test.ts`

### Adding a New API Endpoint

1. Add endpoint method to `lib/rlm-api/client.ts` or create a new module

2. Update OpenAPI types:
   ```bash
   bun run api:sync
   ```

3. Use TanStack Query for data fetching in components

## Related Documentation

- [Developer Setup Guide](developer-setup.md) — Setting up a local development environment
- [Testing Strategy](testing-strategy.md) — Complete testing documentation
- [Frontend Architecture](../reference/frontend-architecture.md) — Detailed component architecture
- [AGENTS.md](../../src/frontend/AGENTS.md) — Frontend-specific conventions and guidelines
