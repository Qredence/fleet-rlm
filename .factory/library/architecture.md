# Architecture

Architectural decisions, patterns discovered, and structure guidelines for the Frontend Architecture Optimization mission.

**What belongs here:** Architectural decisions, patterns discovered, module relationships.

## Directory Structure

```
src/frontend/src/
├── app/           # Application layer (routes, providers, layouts)
│   ├── App.tsx
│   ├── routes.ts
│   ├── layout/    # Shell components (DesktopShell, MobileShell, TopHeader)
│   ├── pages/     # Route page components
│   └── providers/ # Global providers (QueryProvider, AppProviders)
├── components/    # Reusable components
│   ├── ui/        # shadcn/ui primitives only
│   ├── ai-elements/ # AI chat components (official or custom)
│   ├── shared/    # Cross-cutting shared components
│   ├── domain/    # Business-specific components
│   └── chat/      # Chat input components
├── features/      # Feature modules
│   ├── rlm-workspace/ # Chat/runtime UX
│   ├── settings/  # Settings panels and dialogs
│   ├── shell/     # Navigation, command palette, user menu
│   └── volumes/   # Modal Volume browser
├── hooks/         # React hooks (useAuth, useNavigation, etc.)
├── contexts/      # React contexts and providers (after refactor)
├── stores/        # Zustand stores (artifactStore, chatStore)
├── lib/           # Utilities, API clients, config
│   ├── auth/      # Authentication utilities
│   ├── rlm-api/   # Backend API client
│   ├── data/      # Data fetching, mock data
│   └── utils/     # Utility functions (cn, etc.)
├── styles/        # CSS files
│   ├── tailwind.css
│   └── theme.css
└── main.tsx       # Entry point
```

## Key Architectural Decisions

### 1. ai-elements Migration
- Prefer official ai-elements components over custom implementations
- Use `npx ai-elements add <component>` to install
- Keep only truly custom components (sandbox, environment-variables)

### 2. Styling Architecture
- CSS variables in theme.css are the single source of truth
- Tailwind utilities for common patterns
- Inline styles only for dynamic values using CSS variables

### 3. State Management
- TanStack Query for server state
- Zustand for client state (ephemeral UI state)
- React Context for app-wide concerns (auth, navigation)

### 4. Component Organization
- ui/ = primitives only (managed by shadcn CLI)
- ai-elements/ = AI chat components
- features/ = self-contained feature modules
