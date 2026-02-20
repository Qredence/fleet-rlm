# qredence/hax-fleet — Design & Engineering Guidelines

> Skill management platform built with React 18.3.1, Tailwind CSS v4, shadcn/ui,
> Motion (motion/react), Recharts, and React Router v7. Previewed in the
> Figma Make environment.

### Sub-Guidelines

| Guideline                | Path                                                                                     | Purpose                                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Web Interface Guidelines | [`guidelines/skills/web-design-guideline.md`](guidelines/skills/web-design-guideline.md) | Code review checklist for accessibility, animation, forms, typography, performance, and anti-patterns |

---

## 1. Architecture & File Structure

```
src/
  styles/
    fonts.css          # @font-face imports (ONLY place to add font imports)
    tailwind.css       # Tailwind v4 directives, custom @utility definitions
    theme.css          # ALL design tokens (CSS variables), @theme inline block,
                       #   base typography rules, dark mode overrides, glass system,
                       #   data-slot component typography selectors
    index.css          # Import order: fonts -> tailwind -> theme
  app/
    App.tsx            # Root component (must have default export). Renders RouterProvider.
    routes.ts          # React Router v7 data-mode route config (createBrowserRouter)
    providers/
      AppProviders.tsx # Composes AuthProvider + NavigationProvider
    lib/               # Framework-agnostic infrastructure (NOT React components)
      api/             # fleet-rlm HTTP client, endpoint functions, type adapters
        index.ts         # Barrel re-export for convenient single-path imports
        config.ts        # API configuration & isMockMode() environment detection
        client.ts        # Fetch wrapper (auth, timeout, snake↔camel, SSE streaming)
        endpoints.ts     # Typed endpoint functions per REST resource
        types.ts         # Raw backend Pydantic response shapes (Phase 0 verification)
        adapters.ts      # Backend → frontend type transforms + CamelCase utility
    layout/            # Route-level shell infrastructure (NOT reusable components)
      RootLayout.tsx     # Top-level route component: providers + shell selector
      RouteSync.tsx      # URL -> NavigationContext synchronisation
      DesktopShell.tsx   # Resizable split-panel layout (react-resizable-panels)
      MobileShell.tsx    # iOS 26 glass shell with vaul Drawer
      TopHeader.tsx      # Desktop header with nav tabs, theme toggle, user menu
      ChatPanel.tsx      # Left panel: chat + Outlet for child routes
      BuilderPanel.tsx   # Right panel: skill detail, code artifact, taxonomy
    pages/             # Route-level page components (one per route)
      SkillCreationFlow.tsx  # Chat-driven skill creation (index route)
      skill-creation/        # Sub-components: ChatMessageList, AssistantMessage,
                             #   UserMessage, HitlCard, animation-presets, useChatSimulation
      SkillLibrary.tsx       # Card grid with sorting, search, filters
      TaxonomyBrowser.tsx    # Graph + tree taxonomy explorer
      AnalyticsDashboard.tsx # Recharts-powered analytics
      MemoryPage.tsx         # Agent memory browser with bulk actions
      SettingsPage.tsx       # Full-page settings (mobile)
      LoginPage.tsx          # Standalone login (no shell)
      SignupPage.tsx         # Standalone signup (no shell)
      LogoutPage.tsx         # Standalone logout (no shell)
      NotFoundPage.tsx       # 404 fallback
    components/        # Purely reusable building blocks (no route-level files)
      config/
        typo.ts        # Typography inline-style helper (CSS variable refs)
        motion-config.ts # Centralised spring physics (springs, fades, useSpring)
      data/
        mock-data.ts   # Barrel re-export (backward compat — deprecated)
        types.ts       # TypeScript types & interfaces
        mock-skills.ts # Mock data, clarification questions, generated content
        codemirror-theme.ts # CodeMirror theme factory (no static @codemirror/* imports)
        codemirror-modules.ts # CodeMirror barrel for dynamic import
        graph-layouts.ts # Force-directed, cluster, and tree graph layout algorithms
      features/        # Domain-specific components
        settings/      # Decomposed settings panes
          SettingsDialog.tsx      # Shell (Dialog/Drawer + category nav)
          SettingsPaneContent.tsx # Shared pane router for Dialog & Page
          SettingsToggleRow.tsx   # Reusable toggle row for settings panes
          SettingsSelectField.tsx # Reusable select field for settings panes
          types.ts               # Category definitions & types
          GeneralPane.tsx        # General settings pane
          AccountPane.tsx        # Account & team members pane
          BillingPane.tsx        # Billing, payment methods, invoices pane
          NotificationsPane.tsx  # Notifications pane
          PersonalizationPane.tsx # Personalization pane
          DataPrivacyPane.tsx    # Data & Privacy pane
          AboutPane.tsx          # About pane
        ClarificationCard.tsx
        CodeArtifact.tsx   # CodeMirror-powered code viewer/editor
        CommandPalette.tsx # Cmd+K global command palette (cmdk-based)
        ConversationHistory.tsx # Chat history list with time-grouped entries
        CreationPreview.tsx
        FileDetail.tsx     # File content viewer with mock-mode fallback
        IntegrationsDialog.tsx
        LoginDialog.tsx
        NotificationCenter.tsx
        PhaseIndicator.tsx
        PricingDialog.tsx
        SkillBadge.tsx
        SkillCard.tsx
        SkillDetail.tsx
        TaxonomyGraph.tsx  # Canvas-based graph with 3 layout modes
        UserMenu.tsx
      hooks/
        useAppNavigate.ts  # App-level nav hook wrapping React Router useNavigate()
        useAuth.tsx         # Mock auth context + AuthProvider
        useAnalytics.ts     # React Query hook for analytics dashboard
        useChat.ts          # Chat hook with SSE streaming (mock delegates to useChatSimulation)
        useChatHistory.ts   # Conversation history with localStorage persistence
        useCodeMirror.ts    # CodeMirror 6 hook (dynamic imports via loadPkg)
        useFilesystem.ts    # React Query hook for sandbox filesystem data
        useMemory.ts        # React Query hooks for memory entries (CRUD + bulk)
        useNavigation.tsx   # Centralised app state context + NavigationProvider
        useSearch.ts        # Debounced cross-entity search hook
        useSessions.ts      # React Query hooks for session management
        useSkills.ts        # React Query hook for skills list + single + content
        useSkillMutations.ts # Create/update/delete mutations with optimistic updates
        useStickToBottom.ts # Chat auto-scroll hook
        useTaxonomy.ts      # React Query hook for taxonomy tree
        useTheme.ts         # .dark class toggle with localStorage persistence
      shared/            # Reusable presentation components
        ErrorBoundary.tsx      # React error boundary with fallback UI
        LargeTitleHeader.tsx   # iOS 26 collapsing large-title header
        ListRow.tsx            # Compact list row (leading + label + subtitle + trailing)
        ResolvedChip.tsx       # Muted confirmation pill with optional icon
        SectionHeader.tsx      # Icon + label row for card/section headers
        SettingsNavItem.tsx    # Settings sidebar/tab nav button with data-slot typography
        SettingsRow.tsx        # Horizontal settings row (label + description + trailing)
        ToggleSwitch.tsx       # iOS 26 Liquid Glass toggle with spring physics
        SkillMarkdown.tsx      # Lightweight zero-dep markdown renderer
        TypingDots.tsx         # Chat typing indicator
        AnalyticsSkeleton.tsx  # Loading skeletons
        PageSkeleton.tsx
        SkillCardSkeleton.tsx
        SkillLibrarySkeleton.tsx
      ui/                # shadcn/ui primitives + project-specific UI atoms
        animated-indicator.tsx # Motion layoutId pill indicator
        icon-button.tsx        # Plain-function icon button (no forwardRef)
        mobile-tab-bar.tsx     # iOS 26 floating glass tab bar
        nav-tab.tsx            # Desktop header tabs with animated indicator
        panel-tabs.tsx         # Swipeable tabs for BuilderPanel (Motion + gestures)
        prompt-input.tsx       # Two-mode chat composer (collapsed/expanded)
        prompt-plus-menu.tsx   # "+" button popover for toggling features
        prompt-toolbar.tsx     # Chip row inside expanded composer
        queue.tsx              # AI Elements-inspired progressive task list
        reasoning.tsx          # Thinking/reasoning collapsible block
        streamdown.tsx         # Streaming markdown renderer with typewriter effect
        use-mobile.ts          # useIsMobile() hook (768px breakpoint)
        utils.ts               # cn() class-merge utility
        radio-option-card.tsx  # Selectable radio option with animated indicator
        selectable-card.tsx    # Card with selection-mode styling (border, cursor, highlight)
        ...                    # Full shadcn/ui library (40+ primitives)
  imports/               # Figma-exported SVG path data and imported frame components
```

### Key Rules

- `/src/app/App.tsx` is the entry point and must have a `default export`.
- **`lib/`**, **`layout/`**, **`pages/`**, **`components/`**, and **`providers/`** are peer directories at `src/app/`.
- **`lib/`** contains framework-agnostic infrastructure (HTTP client, API config, adapters). NOT React components.
- **`layout/`** and **`pages/`** contain route-level infrastructure and page components respectively. Neither belongs inside `components/`.
- **`components/`** is purely reusable building blocks: `config/`, `data/`, `features/`, `hooks/`, `shared/`, `ui/`. No route-level files live here.
- **`config/`** contains design-system configuration consumed across the project: `typo.ts` (typography inline-style helper) and `motion-config.ts` (spring physics). Imported as `../config/typo` from within `components/`, or `../components/config/typo` from pages/layout.
- **`data/`** contains domain types, mock data, and CodeMirror/graph utilities. API infrastructure lives in `lib/api/`.
- Import conventions:
  - From `pages/` and `layout/`: use `../components/config/`, `../components/data/`, `../components/hooks/`, `../components/ui/`, `../components/features/`, `../components/shared/`, and `../lib/api/`.
  - From `features/`, `shared/`, `ui/` (within components): use `../config/typo`, `../config/motion-config`, `../data/types`, `../data/mock-skills`, `../hooks/*`, `../ui/*`.
  - From `hooks/`: use `../../lib/api/config`, `../../lib/api/endpoints`, `../../lib/api/adapters` for API layer access; `../data/mock-skills` for mock data; `../data/types` for frontend domain types.
  - From `providers/`: use `../lib/api/config`.
  - The barrel `../../lib/api` (index.ts) re-exports everything from config, client, endpoints, types, and adapters for convenience.
- Prefer creating components in `features/`, `shared/`, or `ui/` and importing them into layout or page components.
- Never modify protected files: `ImageWithFallback.tsx`, `pnpm-lock.yaml`.
- Only create `.tsx` files for new components.
- All page components are **statically imported** (no `React.lazy`) — dynamic chunk URLs break in Figma Make when Vite rebuilds.
- `mock-data.ts` is deprecated. Import from the specific module (`types.ts`, `typo.ts`, `mock-skills.ts`).

---

## 2. Routing (React Router v7 Data Mode)

The app uses React Router v7's `createBrowserRouter` with URL as the single source of truth.

### 2.1 Route Structure

```
/login              -> LoginPage (standalone, no shell)
/signup             -> SignupPage (standalone, no shell)
/logout             -> LogoutPage (standalone, no shell)
/404                -> NotFoundPage (standalone, no shell)
/                   -> RootLayout (shell) -> ChatPanel -> Outlet
  (index)           -> SkillCreationFlow (Chat tab)
  /skills           -> SkillLibrary
  /skills/:skillId  -> SkillLibrary (with skill selected in BuilderPanel)
  /taxonomy         -> TaxonomyBrowser
  /taxonomy/:skillId-> TaxonomyBrowser (with skill selected)
  /memory           -> MemoryPage
  /analytics        -> AnalyticsDashboard
  /settings         -> SettingsPage
  *                 -> NotFoundPage
```

### 2.2 Navigation Flow

```
URL change -> RouteSync -> NavigationContext (activeNav, selectedSkill, canvas)
User action -> useAppNavigate().navigateTo() -> react-router navigate() -> URL change
```

- **`useAppNavigate()`** — wraps `useNavigate()` with convenience methods: `navigateTo(nav)`, `navigateToSkill(section, id)`, `navigateToSection(section)`.
- **`RouteSync`** — rendered inside `RootLayout`, watches `useLocation()` and syncs `NavigationContext` (one-way: URL -> context).
- **`pathToNav()`** / **`navToPath()`** — mapping helpers between `NavItem` and URL paths.

### 2.3 Context Architecture

All React contexts use `createContext<T>(defaultValue)` with no-op defaults so components can be rendered outside providers during testing.

```tsx
// providers/AppProviders.tsx composes:
AuthProvider      -> useAuth() hook (mock login/logout, user profile, plan tier)
NavigationProvider -> useNavigation() hook (activeNav, canvas, selectedSkill, prompt state)
```

---

## 3. Design System Token Architecture

**Every visual value in the UI must come from CSS custom properties defined in
`/src/styles/theme.css`.** This ensures the entire UI is re-themeable by editing
a single CSS file.

### 3.1 Colors

Use Tailwind utility classes that reference the `@theme inline` mappings:

| Token                                | Tailwind class                           | Purpose                             |
| ------------------------------------ | ---------------------------------------- | ----------------------------------- |
| `--background`                       | `bg-background`                          | App background                      |
| `--foreground`                       | `text-foreground`                        | Primary text                        |
| `--card` / `--card-foreground`       | `bg-card` / `text-card-foreground`       | Card surfaces                       |
| `--primary` / `--primary-foreground` | `bg-primary` / `text-primary-foreground` | Primary buttons, CTAs               |
| `--secondary`                        | `bg-secondary`                           | Secondary buttons, muted surfaces   |
| `--muted` / `--muted-foreground`     | `bg-muted` / `text-muted-foreground`     | Disabled/subdued elements           |
| `--accent` / `--accent-foreground`   | `bg-accent` / `text-accent`              | Highlights, active states           |
| `--destructive`                      | `bg-destructive`                         | Error/delete actions                |
| `--border`                           | `border-border`                          | Default borders                     |
| `--border-subtle`                    | `border-border-subtle`                   | Low-emphasis dividers, nested edges |
| `--border-strong`                    | `border-border-strong`                   | Prominent separators, active inputs |
| `--ring`                             | `ring-ring`                              | Focus rings                         |
| `--input` / `--input-background`     | `bg-input`                               | Form input fills                    |
| `--bg-elevated-primary`              | `bg-elevated-primary`                    | Elevated surface backgrounds        |
| `--chart-1` through `--chart-5`      | `text-chart-1`, etc.                     | Recharts data series                |
| `--sidebar-*`                        | `bg-sidebar`, etc.                       | Sidebar-specific tokens             |

For inline styles where Tailwind classes are not mapped, reference the CSS variable directly:

```tsx
style={{ backgroundColor: 'var(--bg-elevated-primary)' }}
```

### 3.2 Spacing & Radius

| Token              | Tailwind class    | Value            | Usage                            |
| ------------------ | ----------------- | ---------------- | -------------------------------- |
| `--radius`         | `rounded-lg`      | `8px`            | Default element radius           |
| `--radius-button`  | `rounded-button`  | `999px` (pill)   | All buttons                      |
| `--radius-card`    | `rounded-card`    | `24px`           | Cards, dialog, drawer            |
| `--radius-card-lg` | `rounded-card-lg` | `28px`           | Suggestion cards, large surfaces |
| `--radius-hero`    | `rounded-hero`    | `32px`           | Hero images, composer            |
| `--radius-sm`      | `rounded-sm`      | `calc(radius-4)` | Small elements                   |
| `--radius-md`      | `rounded-md`      | `calc(radius-2)` | Medium elements (tabs, etc.)     |
| `--radius-xl`      | `rounded-xl`      | `calc(radius+4)` | Extra-large surfaces             |

### 3.3 Elevation / Shadows

| Token                   | Tailwind class | Purpose                            |
| ----------------------- | -------------- | ---------------------------------- |
| `--elevation-sm`        | `shadow-sm`    | Cards, resting containers          |
| `--elevation-md`        | `shadow-md`    | Hovered/interactive cards          |
| `--shadow-200-stronger` | _(inline)_     | Prominent surfaces (chat composer) |

---

## 4. Typography System

### 4.1 Font Families

Defined in `theme.css`:

```css
--font-family:
  -apple-system, BlinkMacSystemFont, "SF Pro Display",
  "SF Pro Text", Inter, system-ui, sans-serif;
--font-family-mono:
  ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
  "Liberation Mono", monospace;
```

**Rule:** Only use these two font stacks. Never add `font-sans`, `font-mono`, or
hardcoded `font-family` values in components. Font imports go exclusively in
`/src/styles/fonts.css`.

### 4.2 Type Scale (CSS Variables)

| Variable         | Size | Usage                                |
| ---------------- | ---- | ------------------------------------ |
| `--text-h1`      | 36px | Page hero headings                   |
| `--text-display` | 32px | Display/marketing headings           |
| `--text-h2`      | 24px | Section headings, mobile large title |
| `--text-h3`      | 18px | Sub-section headings, dialog titles  |
| `--text-h4`      | 17px | Card titles, inline headings         |
| `--text-base`    | 17px | Body text, chat messages             |
| `--text-label`   | 14px | Buttons, inputs, labels, nav tabs    |
| `--text-caption` | 13px | Subtitles, metadata                  |
| `--text-helper`  | 12px | Badges, tooltips, helper text        |
| `--text-micro`   | 10px | Tab bar labels, chart tick labels    |

### 4.3 Font Weights

| Variable                 | Value | Usage                              |
| ------------------------ | ----- | ---------------------------------- |
| `--font-weight-semibold` | 600   | Headings (h1-h3)                   |
| `--font-weight-medium`   | 500   | Labels, buttons, h4, active states |
| `--font-weight-regular`  | 400   | Body text, captions, inputs        |

### 4.4 The `typo` Helper Object

All component typography MUST use the shared `typo` helper from
`/src/app/components/config/typo.ts` applied via inline `style`:

```tsx
import { typo } from '../config/typo';

// Apply typography via inline style — never use Tailwind text-* or font-* classes
<h2 style={typo.h2}>Section Title</h2>
<p style={typo.base}>Body text here.</p>
<span style={typo.caption}>Metadata</span>
<code style={typo.mono}>console.log()</code>
```

Available `typo` keys: `h1`, `display`, `h2`, `h3`, `h4`, `base`, `label`,
`labelRegular`, `caption`, `helper`, `micro`, `mono`.

Each key maps to an object with `fontSize`, `fontWeight`, `fontFamily`, and
`lineHeight` — all referencing CSS variables.

### 4.5 Base Typography via `data-slot`

`theme.css` styles all shadcn primitives via `data-slot` selectors. The
following components receive their typography automatically and do **not**
need redundant `style={typo.*}` or Tailwind font classes:

**Cards:** `card-title`, `card-description`
**Buttons:** `button`, `badge`
**Forms:** `input`, `input-otp-slot`, `label`, `textarea`, `form-label`, `form-description`, `form-message`
**Dialogs:** `dialog-title`, `dialog-description`, `drawer-title`, `drawer-description`, `sheet-title`, `sheet-description`, `alert-dialog-title`, `alert-dialog-description`
**Menus:** `dropdown-menu-*`, `context-menu-*`, `menubar-*`, `navigation-menu-*`
**Data:** `table`, `table-head`, `table-caption`, `table-footer`
**Tabs:** `tabs-trigger`
**Charts:** `chart`, `chart-tooltip-content`, `chart-tooltip-label`
**Sidebar:** `sidebar-group-label`, `sidebar-group-content`, `sidebar-menu-button` (with `[data-active]` and `[data-size]` variants), `sidebar-menu-badge`, `sidebar-menu-sub-button` (with `[data-size]` variants)
**Other:** `accordion-trigger`, `accordion-content`, `alert`, `alert-title`, `alert-description`, `breadcrumb-list`, `breadcrumb-page`, `command-input`, `command-empty`, `command-group`, `command-item`, `command-shortcut`, `select-trigger`, `select-item`, `select-label`, `toggle`, `tooltip-content`
**Custom:** `queue-section-label`, `queue-item-content`, `queue-item-description`
**Settings:** `settings-row-label`, `settings-row-description`, `settings-row-value`, `settings-nav-item-label`
**Lists:** `list-row-label`, `list-row-subtitle`
**Extraction:** `section-header`, `resolved-chip`, `resolved-chip-label`, `radio-option-card-label`, `radio-option-card-description`
**Calendar:** `.calendar-caption-label`, `.calendar-head-cell`, `.calendar-cell`, `.calendar-day` (CSS classes, not data-slot)

### 4.6 Typography Anti-Patterns

- **Never** use Tailwind typography utilities: `text-sm`, `text-lg`, `text-2xl`,
  `font-bold`, `font-medium`, `leading-tight`, etc.
- **Never** hardcode pixel font sizes or numeric font weights in components.
- **Never** add `font-family` to individual components — it comes from the
  base `body` rule and the `typo` helper.

---

## 5. Component Patterns

### 5.1 shadcn/ui Primitives

The project uses the full shadcn/ui component library. Always prefer these
over custom implementations:

| Primitive    | Import from         | Notes                                                                                                                                       |
| ------------ | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `Button`     | `../ui/button`      | Pill shape via `rounded-button`. Variants: `default`, `secondary`, `outline`, `ghost`, `accent`, `destructive`, `destructive-ghost`, `link` |
| `Badge`      | `../ui/badge`       | Variants: `default`, `secondary`, `outline`, `accent`, `success`, `warning`, `destructive-subtle`                                           |
| `Card`       | `../ui/card`        | Compound: `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter`, `CardAction`                                   |
| `Input`      | `../ui/input`       | Uses `--radius: 8px` (`rounded-lg`)                                                                                                         |
| `ScrollArea` | `../ui/scroll-area` | Radix-based, fixes applied in `theme.css`                                                                                                   |
| `Tabs`       | `../ui/tabs`        | `TabsList`, `TabsTrigger`, `TabsContent`                                                                                                    |
| `Progress`   | `../ui/progress`    | Quality bars, loading indicators                                                                                                            |
| `Tooltip`    | `../ui/tooltip`     | See IconButton ref pattern below                                                                                                            |
| `Select`     | `../ui/select`      | Compound: `SelectTrigger`, `SelectContent`, `SelectItem`                                                                                    |
| `Dialog`     | `../ui/dialog`      | `rounded-card` on `DialogContent`                                                                                                           |
| `Separator`  | `../ui/separator`   | Section dividers                                                                                                                            |
| `Drawer`     | `vaul`              | Mobile bottom sheet (iOS 26 glass material)                                                                                                 |

### 5.2 Project-Specific UI Atoms

| Component           | Path                         | Notes                                                                                 |
| ------------------- | ---------------------------- | ------------------------------------------------------------------------------------- |
| `IconButton`        | `../ui/icon-button`          | Plain function declaration (no forwardRef). Uses `data-slot="icon-button"`            |
| `NavTab`            | `../ui/nav-tab`              | Desktop header tabs with animated indicator                                           |
| `AnimatedIndicator` | `../ui/animated-indicator`   | Motion `layoutId` pill indicator                                                      |
| `PanelTabs`         | `../ui/panel-tabs`           | Swipeable tabs with Motion gestures for BuilderPanel                                  |
| `PromptInput`       | `../ui/prompt-input`         | Two-mode chat composer (collapsed pill / expanded multi-line) with 44pt touch targets |
| `PromptPlusMenu`    | `../ui/prompt-plus-menu`     | Popover from "+" button for toggling features                                         |
| `PromptToolbar`     | `../ui/prompt-toolbar`       | Chip row inside expanded composer                                                     |
| `Queue`             | `../ui/queue`                | AI Elements-inspired progressive task list                                            |
| `Reasoning`         | `../ui/reasoning`            | Collapsible thinking/reasoning block                                                  |
| `Streamdown`        | `../ui/streamdown`           | Streaming markdown renderer with typewriter effect                                    |
| `MobileTabBar`      | `../ui/mobile-tab-bar`       | iOS 26 floating glass tab bar                                                         |
| `ToggleSwitch`      | `../shared/ToggleSwitch`     | iOS 26 Liquid Glass toggle with spring physics                                        |
| `LargeTitleHeader`  | `../shared/LargeTitleHeader` | iOS 26 collapsing large-title header                                                  |
| `SkillMarkdown`     | `../shared/SkillMarkdown`    | Lightweight zero-dep markdown renderer                                                |
| `SkillBadge`        | `../features/SkillBadge`     | Badge wrapper with `rounded-lg` override                                              |
| `ErrorBoundary`     | `../shared/ErrorBoundary`    | React error boundary with fallback UI                                                 |
| `SelectableCard`    | `../ui/selectable-card`      | Card with selection-mode border/cursor/highlight. `data-slot="selectable-card"`        |
| `RadioOptionCard`   | `../ui/radio-option-card`    | Radio option with animated dot. `data-slot="radio-option-card"`                       |
| `SettingsRow`       | `../shared/SettingsRow`      | Horizontal settings row (label + desc + trailing). `data-slot="settings-row-*"`       |
| `ListRow`           | `../shared/ListRow`          | Compact list row (leading + label + subtitle + trailing). `data-slot="list-row-*"`    |
| `SettingsNavItem`   | `../shared/SettingsNavItem`  | Settings sidebar/tab button with active state. `data-slot="settings-nav-item-*"`      |
| `SectionHeader`     | `../shared/SectionHeader`    | Icon + label section header. `data-slot="section-header"`                             |
| `ResolvedChip`      | `../shared/ResolvedChip`     | Muted confirmation pill with optional icon. `data-slot="resolved-chip"`               |

### 5.3 `cn()` Utility

Always use `cn()` (from `../ui/utils`) for class merging:

```tsx
import { cn } from "../ui/utils";

<div
  className={cn(
    "base-classes",
    isActive && "active-class",
    className,
  )}
/>;
```

### 5.4 `data-slot` Attributes

All shadcn/ui primitives carry `data-slot` attributes. These are used in
`theme.css` for centralised typography styling. Preserve them on all
components that use them.

---

## 6. iOS 26 Liquid Glass Design Language

The mobile UX follows Apple's iOS 26 Liquid Glass material system.

### 6.1 Glass CSS Variables

All glass materials are driven by CSS variables (light + dark mode):

| Variable                 | Purpose                           |
| ------------------------ | --------------------------------- |
| `--glass-tab-bg`         | Tab bar background (translucent)  |
| `--glass-tab-border`     | Tab bar border                    |
| `--glass-tab-shadow`     | Tab bar multi-layer box-shadow    |
| `--glass-tab-highlight`  | Top-edge specular rim highlight   |
| `--glass-tab-blur`       | Tab bar backdrop-filter blur      |
| `--glass-tab-bar-height` | Tab bar height (56px)             |
| `--glass-tab-bar-radius` | Tab bar border-radius (28px pill) |
| `--glass-tab-bar-inset`  | Inset from screen edges (12px)    |
| `--glass-nav-bg`         | Navigation header background      |
| `--glass-nav-border`     | Navigation header border          |
| `--glass-nav-blur`       | Navigation header blur            |
| `--glass-sheet-bg`       | Bottom sheet background           |
| `--glass-sheet-border`   | Bottom sheet border               |
| `--glass-sheet-blur`     | Bottom sheet blur (40px)          |
| `--glass-sheet-handle`   | Drawer grab handle color          |
| `--glass-overlay`        | Overlay/scrim behind sheets       |

### 6.2 Glass Material Application

Apply glass materials via inline `style` since they use `backdrop-filter` and
CSS variable composition that Tailwind cannot express:

```tsx
style={{
  backgroundColor: 'var(--glass-nav-bg)',
  backdropFilter: 'blur(var(--glass-nav-blur))',
  WebkitBackdropFilter: 'blur(var(--glass-nav-blur))',
  borderBottom: '0.5px solid var(--glass-nav-border)',
}}
```

### 6.3 Haptic-Press Feedback

CSS-driven press-down effect on touch devices. Controlled by:

```css
--haptic-scale: 0.97;
--haptic-duration: 0.12s;
```

Applied globally in `theme.css` via `@media (pointer: coarse)` on `button`,
`a`, `[role="button"]`, `[role="tab"]`, `[data-slot="button"]`, and
`[data-slot="icon-button"]`. No per-component code needed.

### 6.4 Toggle Switch (Liquid Glass Knob)

The `ToggleSwitch` component implements a fully authentic iOS 26 toggle with:

- 6-layer glass knob material (specular, rim, depth, caustic, color bleed, frosted)
- Spring-based snap: `stiffness: 500, damping: 35, mass: 0.8`
- Press-hold deformation: `scaleX: 1.22`
- Draggable with elastic constraints
- `--toggle-*` CSS variables for full theme control

---

## 7. Motion & Animation

### 7.1 Library

Use `motion/react` (NOT "framer-motion"):

```tsx
import { motion, AnimatePresence } from "motion/react";
```

### 7.2 Spring Physics Constants

The project standard spring configuration lives in
`/src/app/components/config/motion-config.ts`. **Never inline spring values —
always import from this module.**

| `springs` Key  | Stiffness | Damping | Mass | Usage                                          |
| -------------- | --------- | ------- | ---- | ---------------------------------------------- |
| `default`      | 380       | 30      | 0.8  | General UI transitions (panels, cards, modals) |
| `indicator`    | 350       | 30      | 0.8  | Navigation tab indicator pill                  |
| `snappy`       | 500       | 30      | 0.8  | Quick feedback (card press, counter pop)       |
| `toggleSnap`   | 500       | 35      | 0.8  | `ToggleSwitch` positional snap                 |
| `toggleDeform` | 450       | 28      | --   | `ToggleSwitch` shape deformation               |
| `instant`      | --        | --      | --   | `{ duration: 0.01 }` reduced-motion fallback   |

Easing-based fades (non-spring) are in `fades`:

| `fades` Key | Duration | Usage                                  |
| ----------- | -------- | -------------------------------------- |
| `fast`      | 0.12     | Page/content route opacity transitions |
| `collapse`  | 0.18     | Tree node / accordion expand/collapse  |
| `instant`   | 0.01     | Reduced-motion fallback                |

```tsx
import { springs, fades, useSpring } from '../config/motion-config';

// Direct usage:
<motion.div transition={prefersReduced ? springs.instant : springs.default} />

// With stagger delay:
<motion.div transition={prefersReduced ? springs.instant : { ...springs.default, delay: i * 0.02 }} />

// Hook usage (simplest — handles reduced-motion automatically):
const spring = useSpring('indicator');
<motion.div transition={spring} />
```

Use `type: 'spring'` for all interactive transitions. Reserve `ease` curves
for simple opacity/color fades.

### 7.3 Animation Presets

Shared entrance/exit presets live in `pages/skill-creation/animation-presets.ts`:

```tsx
import { fadeUp, fadeUpReduced } from "./animation-presets";

const prefersReduced = useReducedMotion();
<motion.div {...(prefersReduced ? fadeUpReduced : fadeUp)} />;
```

| Preset          | Initial           | Animate          | Transition        |
| --------------- | ----------------- | ---------------- | ----------------- |
| `fadeUp`        | `opacity:0, y:12` | `opacity:1, y:0` | `springs.default` |
| `fadeUpReduced` | `opacity:0`       | `opacity:1`      | `springs.instant` |

### 7.4 `prefers-reduced-motion` Support

**Global CSS:** `theme.css` includes a `@media (prefers-reduced-motion: reduce)`
block that kills all CSS animations and transitions.

**Motion components:** Use `useReducedMotion()` from `motion/react` and
reference `springs.instant` for the fallback, or use the `useSpring` hook:

```tsx
import { useReducedMotion } from "motion/react";
import { springs, useSpring } from "../config/motion-config";

// Option A: when you need prefersReduced for more than just transition
const prefersReduced = useReducedMotion();
const spring = prefersReduced
  ? springs.instant
  : springs.default;

// Option B: when you only need the transition value
const spring = useSpring("default"); // handles reduced-motion internally
```

### 7.5 `AnimatePresence`

Wrap conditional renders in `<AnimatePresence>` for exit animations.
Always provide a unique `key` on the animated child.

### 7.6 `LayoutGroup` and `layoutId`

Used for shared-layout animations (e.g., nav tab indicator). Wrap related
elements in `<LayoutGroup id="uniqueId">`.

---

## 8. Environment Constraints (Figma Make + React 18.3.1)

### 8.1 HMR Crash: `const` + `React.forwardRef`

**Problem:** `const Component = React.forwardRef(...)` with an arrow function crashes
Hot Module Replacement in the Figma Make preview.

**Solution:** Use **plain function declarations** for all components:

```tsx
// CORRECT — plain function declaration
function MyComponent({ ...props }: Props) { ... }
export { MyComponent };

// CORRECT — forwardRef with named function expression
const Button = React.forwardRef<HTMLButtonElement, Props>(
  function Button({ ...props }, ref) { ... }
);
```

### 8.2 IconButton + Tooltip Ref Pattern

`IconButton` is a plain function (no `forwardRef`), so Radix `TooltipTrigger asChild`
cannot attach a ref directly to it.

**Pattern:** Wrap `IconButton` in a native `<span className="inline-flex">`:

```tsx
<Tooltip>
  <TooltipTrigger asChild>
    <span className="inline-flex">
      <IconButton aria-label="Action" onClick={handler}>
        <Icon className="size-5" />
      </IconButton>
    </span>
  </TooltipTrigger>
  <TooltipContent side="bottom">Action</TooltipContent>
</Tooltip>
```

### 8.3 No `React.lazy()` — Static Imports Only

**Problem:** Vite chunk URLs go stale when Figma Make rebuilds, causing
"Failed to fetch dynamically imported module" errors.

**Solution:** All page components are statically imported in `routes.ts`.
No `React.lazy()` or `Suspense` code splitting.

### 8.4 No `src/main.tsx`

The Figma Make platform mounts `App.tsx` directly. There is no `src/main.tsx` file.

### 8.5 CodeMirror Dynamic Imports

**Problem:** Vite's Babel/SWC transform strips `/* @vite-ignore */` magic comments
in Figma Make, so standard dynamic imports of `@codemirror/*` and `@lezer/*` fail.

**Solution:** The `useCodeMirror` hook uses a `loadPkg(specifier)` helper that
passes specifiers as **variable references** to `import()`, which prevents
`es-module-lexer` from extracting them as static strings. The `codemirror-theme.ts`
file uses a **factory pattern** — it receives loaded CM modules as arguments
rather than importing them at the top level.

---

## 9. Dark Mode

### 9.1 Implementation

- Theme toggle via `useTheme()` hook (adds/removes `.dark` class on `<html>`, sets `colorScheme`).
- `.dark` class overrides are defined in `theme.css` under the `.dark` selector.
- Tailwind's `@custom-variant dark (&:is(.dark *))` enables `dark:` prefix classes.
- Smooth 250ms theme transition via `.theme-transition` class (added/removed by `useTheme`).
- Persisted to `localStorage` under key `theme`.

### 9.2 Rules

- **Colors must use Tailwind token classes** (`bg-background`, `text-foreground`,
  etc.) which automatically adapt to dark mode.
- For inline styles, always reference CSS variables (`var(--foreground)`) which
  are overridden by the `.dark` selector.
- Never use hardcoded `rgba()`, `#hex`, or `hsl()` values in components.

---

## 10. Responsive & Mobile UX

### 10.1 Breakpoint Detection

Use the `useIsMobile()` hook from `../ui/use-mobile` (768px breakpoint) to
conditionally render mobile vs. desktop layouts.

### 10.2 Mobile Layout Pattern

```
+---------------------------+
| Glass Nav Header          |  <- --glass-nav-* vars, backdrop-filter
+---------------------------+
|                           |
|   Main content area       |  <- Full viewport, scrollable
|   (LargeTitleHeader +     |
|    page content)          |
|                           |
+---------------------------+
| Glass Tab Bar (floating)  |  <- --glass-tab-* vars, env(safe-area-inset-bottom)
+---------------------------+
```

- Bottom sheet via `vaul` `Drawer` with glass material
- Safe area insets: `env(safe-area-inset-*)` for notch/home indicator
- Touch targets: minimum `44px` (Apple HIG) via the `touch-target` CSS utility class (defined in `tailwind.css`)
- `touch-action: manipulation` applied globally in `theme.css`
- `overscroll-behavior: contain` on scroll areas (prevents rubber-band bleed)

### 10.3 Desktop Layout Pattern

```
+--------------------------------------------------+
| TopHeader (solid bg, inline nav tabs)             |
+------------------------+-------------------------+
| ChatPanel              | BuilderPanel             |
| (resizable, contains   | (collapsible, animated,  |
|  Outlet for routes)    |  PanelTabs for content)  |
+------------------------+-------------------------+
```

- `react-resizable-panels` for the split-panel layout
- CSS `flex-grow` transition (350ms cubic-bezier) for smooth panel open/close
- Transition disabled during manual drag (via `onDragging` callback)
- `BuilderPanel` uses `PanelTabs` (swipeable, Motion-powered) for sub-views

---

## 11. Accessibility

### 11.1 Required Patterns

- Icon-only buttons: always include `aria-label`
- Form controls: `<label>` or `aria-label`
- Use semantic HTML: `<button>` for actions, `<a>` for navigation
- Images: `alt` attribute (or `alt=""` for decorative)
- Decorative icons: `aria-hidden="true"`
- Focus states: `focus-visible:ring-*` (never remove `outline` without replacement)
- Headings: maintain hierarchical `h1` > `h2` > `h3` order
- Lists: unique `key` prop on every mapped element
- Role attributes: `role="switch"` on toggles, `role="tab"` / `role="tablist"` on tab bars

### 11.2 Keyboard Navigation

- All interactive elements must be keyboard-accessible
- Toggles respond to `Space` and `Enter`
- Tab order follows visual flow

### 11.3 Focus Ring System

Standard focus ring (from `theme.css` base rule):

```
outline-ring/50
focus-visible:ring-[2px] focus-visible:ring-ring/50
```

---

## 12. Charts (Recharts)

### 12.1 Color Tokens

Use `--chart-1` through `--chart-5` for data series. Reference via CSS:

```tsx
<Bar fill="var(--chart-1)" />
<Line stroke="var(--chart-2)" />
```

### 12.2 Tick Label Typography

Chart axis labels must use design system typography:

```tsx
tick={{
  fontSize: 'var(--text-micro)',
  fontFamily: 'var(--font-family)',
  fill: 'var(--muted-foreground)',
}}
```

---

## 13. SVG & Image Handling

### 13.1 Figma-Exported SVGs

SVGs are stored as path-data modules in `/src/imports/svg-*.ts`.
Import and use inside `<svg>` elements:

```tsx
import svgPaths from "@/imports/svg-g9pgfbkia";

<svg viewBox="0 0 18 17" fill="none">
  <path d={svgPaths.p4dc2a80} fill="var(--foreground)" />
</svg>;
```

Always use `fill="var(--foreground)"` or `fill="currentColor"` for theme
compatibility. Never duplicate or recreate imported SVGs.

### 13.2 Raster Images

Use the `figma:asset` scheme for Figma-imported raster images:

```tsx
import img from "figma:asset/abc123.png"; // No ./ or ../ prefix!
```

For new images, use the `ImageWithFallback` component instead of `<img>`.

---

## 14. Coding Standards

### 14.1 Component Declaration

```tsx
// Preferred: plain function declaration
export function MyComponent({ prop1, prop2 }: MyComponentProps) {
  return ( ... );
}

// Alternative for non-exported:
function HelperComponent() { ... }

// forwardRef exception: named function expression
const Button = React.forwardRef<HTMLButtonElement, Props>(
  function Button({ ...props }, ref) { ... }
);
```

### 14.2 Imports

```tsx
// React
import { useState, useCallback } from "react";

// React Router
import { useNavigate, useLocation } from "react-router";

// Motion
import {
  motion,
  AnimatePresence,
  useReducedMotion,
} from "motion/react";

// Project config
import {
  springs,
  fades,
  useSpring,
} from "../config/motion-config";
import { typo } from "../config/typo";
import type { Skill, NavItem } from "../data/types";

// Project hooks
import { useNavigation } from "../hooks/useNavigation";
import { useAppNavigate } from "../hooks/useAppNavigate";
import { useAuth } from "../hooks/useAuth";

// Project UI
import { Button } from "../ui/button";
import { cn } from "../ui/utils";

// Figma assets
import svgPaths from "@/imports/svg-xxxxx";
```

### 14.3 Styling Priority

1. **Tailwind classes** for layout, spacing, display, colors (using token classes)
2. **`cn()`** for conditional class merging
3. **`style={typo.xxx}`** for all typography
4. **Inline `style`** for glass materials, dynamic values, and CSS variables
   not mapped to Tailwind
5. **Never:** hardcoded colors, pixel font sizes, numeric font weights, `rgba()`
   in components, Tailwind typography utilities (`text-sm`, `font-bold`, etc.)

### 14.4 File Size

Keep files small. Extract helper functions and sub-components into their own
files. Feature components live in `features/`, reusable atoms in `shared/` or
`ui/`.

---

## 15. Remaining Backlog

- [x] Register `--bg-elevated-primary` in `@theme inline` block
- [x] Split overloaded `mock-data.ts` into separate type/data modules
- [x] Add React error boundaries around page-level components
- [x] Add static imports for page components (replaced React.lazy approach)
- [x] Skill Library sorting functionality
- [x] URL-based routing migration (React Router v7 data mode)
- [x] Command Palette (Cmd+K)
- [x] Notification Center
- [x] CodeMirror integration in CodeArtifact
- [x] TaxonomyGraph: node drag-to-reposition, three layout modes
- [x] PromptInput two-mode composer
- [x] Decomposed SettingsDialog into panes
- [x] Mock user authentication features
- [x] Design system audit: removed Tailwind typography classes from 14 shadcn/ui files
- [x] Added comprehensive `data-slot` typography selectors in theme.css
- [x] Added `[data-slot][data-size]` sidebar variant selectors
- [x] Fixed hardcoded `#888` in TaxonomyGraph with `--muted-foreground` RGB
- [x] Added `prefers-reduced-motion` to HitlCard with fadeUpReduced preset
- [x] Phase 1: SectionHeader, ResolvedChip, RadioOptionCard, touch-target CSS utility extraction
- [x] Phase 2: SelectableCard extraction (ui/selectable-card.tsx, applied to MemoryPage)
- [x] Phase 3: Layout row extractions (SettingsRow, ListRow, SettingsNavItem) + SettingsPaneContent shared router
- [x] Phase 4: Settings page cleanup (DRY SettingsDialog + SettingsPage via SettingsPaneContent, data-slot typography migration)
- [x] Global `prefers-reduced-motion` audit — all 25 Motion-importing files verified (useReducedMotion direct or useSpring hook)
- [x] Chat conversation history — localStorage-backed with auto-save, time-grouped list, load/delete/clear

All backlog items complete.