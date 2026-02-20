# qredence/hax-fleet — Design & Engineering Guidelines

> Skill management platform built with React 18.3.1, Tailwind CSS v4, shadcn/ui,
> Motion (motion/react), Recharts, and React Router v7. Previewed in the
> Figma Make environment.

### Sub-Guidelines

| Guideline                | Path                                                                 | Purpose                                                                                               |
| ------------------------ | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Web Interface Guidelines | [`skills/web-design-guideline.md`](skills/web-design-guideline.md)   | Code review checklist for accessibility, animation, forms, typography, performance, and anti-patterns |

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
      data/
        mock-data.ts   # Barrel re-export (backward compat -- deprecated)
        types.ts       # TypeScript types & interfaces
        typo.ts        # Typography inline-style helper (CSS variable refs)
        mock-skills.ts # Mock data, clarification questions, generated content
        motion-config.ts # Centralised spring physics (springs, fades, useSpring)
        codemirror-theme.ts # CodeMirror theme factory (no static @codemirror/* imports)
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
        useChatHistory.ts   # Conversation history with localStorage persistence
        useCodeMirror.ts    # CodeMirror 6 hook (dynamic imports via loadPkg)
        useNavigation.tsx   # Centralised app state context + NavigationProvider
        useStickToBottom.ts # Chat auto-scroll hook
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
- **`layout/`** and **`pages/`** are peer directories at `src/app/` -- they contain route-level infrastructure and page components respectively. Neither belongs inside `components/`.
- **`components/`** is purely reusable building blocks: `data/`, `features/`, `hooks/`, `shared/`, `ui/`. No route-level files live here.
- Import conventions from `pages/` and `layout/` files: use `../components/data/`, `../components/hooks/`, `../components/ui/`, `../components/features/`, `../components/shared/` to reach into the components directory.
- Prefer creating components in `features/`, `shared/`, or `ui/` and importing them into layout or page components.
- Never modify protected files: `ImageWithFallback.tsx`, `pnpm-lock.yaml`.
- Only create `.tsx` files for new components.
- All page components are **statically imported** (no `React.lazy`) -- dynamic chunk URLs break in Figma Make when Vite rebuilds.
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

- **`useAppNavigate()`** -- wraps `useNavigate()` with convenience methods: `navigateTo(nav)`, `navigateToSkill(section, id)`, `navigateToSection(section)`.
- **`RouteSync`** -- rendered inside `RootLayout`, watches `useLocation()` and syncs `NavigationContext` (one-way: URL -> context).
- **`pathToNav()`** / **`navToPath()`** -- mapping helpers between `NavItem` and URL paths.

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

| Token                    | Tailwind class              | Purpose                               |
|--------------------------|-----------------------------|---------------------------------------|
| `--background`           | `bg-background`             | App background                        |
| `--foreground`           | `text-foreground`           | Primary text                          |
| `--card` / `--card-foreground` | `bg-card` / `text-card-foreground` | Card surfaces                |
| `--primary` / `--primary-foreground` | `bg-primary` / `text-primary-foreground` | Primary buttons, CTAs |
| `--secondary`            | `bg-secondary`              | Secondary buttons, muted surfaces     |
| `--muted` / `--muted-foreground` | `bg-muted` / `text-muted-foreground` | Disabled/subdued elements |
| `--accent` / `--accent-foreground` | `bg-accent` / `text-accent` | Highlights, active states           |
| `--destructive`          | `bg-destructive`            | Error/delete actions                  |
| `--border`               | `border-border`             | Default borders                       |
| `--border-subtle`        | `border-border-subtle`      | Low-emphasis dividers, nested edges   |
| `--border-strong`        | `border-border-strong`      | Prominent separators, active inputs   |
| `--ring`                 | `ring-ring`                 | Focus rings                           |
| `--input` / `--input-background` | `bg-input`           | Form input fills                      |
| `--bg-elevated-primary`  | `bg-elevated-primary`       | Elevated surface backgrounds          |
| `--chart-1` through `--chart-5` | `text-chart-1`, etc.  | Recharts data series                  |
| `--sidebar-*`            | `bg-sidebar`, etc.          | Sidebar-specific tokens               |

For inline styles where Tailwind classes are not mapped, reference the CSS variable directly:

```tsx
style={{ backgroundColor: 'var(--bg-elevated-primary)' }}
```

### 3.2 Spacing & Radius

| Token              | Tailwind class    | Value             | Usage                        |
|--------------------|-------------------|-------------------|------------------------------|
| `--radius`         | `rounded-lg`      | `8px`             | Default element radius       |
| `--radius-button`  | `rounded-button`  | `999px` (pill)    | All buttons                  |
| `--radius-card`    | `rounded-card`    | `24px`            | Cards, dialog, drawer        |
| `--radius-card-lg` | `rounded-card-lg` | `28px`            | Suggestion cards, large surfaces |
| `--radius-hero`    | `rounded-hero`    | `32px`            | Hero images, composer        |
| `--radius-sm`      | `rounded-sm`      | `calc(radius-4)`  | Small elements               |
| `--radius-md`      | `rounded-md`      | `calc(radius-2)`  | Medium elements (tabs, etc.) |
| `--radius-xl`      | `rounded-xl`      | `calc(radius+4)`  | Extra-large surfaces         |

### 3.3 Elevation / Shadows

| Token                    | Tailwind class | Purpose                              |
|--------------------------|----------------|--------------------------------------|
| `--elevation-sm`         | `shadow-sm`    | Cards, resting containers            |
| `--elevation-md`         | `shadow-md`    | Hovered/interactive cards            |
| `--shadow-200-stronger`  | *(inline)*     | Prominent surfaces (chat composer)   |

---

## 4. Typography System

### 4.1 Font Families

Defined in `theme.css`:

```css
--font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
               "SF Pro Text", Inter, system-ui, sans-serif;
--font-family-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo,
                    Consolas, "Liberation Mono", monospace;
```

**Rule:** Only use these two font stacks. Never add `font-sans`, `font-mono`, or
hardcoded `font-family` values in components. Font imports go exclusively in
`/src/styles/fonts.css`.

### 4.2 Type Scale (CSS Variables)

| Variable              | Size   | Usage                                    |
|-----------------------|--------|------------------------------------------|
| `--text-h1`           | 36px   | Page hero headings                       |
| `--text-display`      | 32px   | Display/marketing headings               |
| `--text-h2`           | 24px   | Section headings, mobile large title     |
| `--text-h3`           | 18px   | Sub-section headings, dialog titles      |
| `--text-h4`           | 17px   | Card titles, inline headings             |
| `--text-base`         | 17px   | Body text, chat messages                 |
| `--text-label`        | 14px   | Buttons, inputs, labels, nav tabs        |
| `--text-caption`      | 13px   | Subtitles, metadata                      |
| `--text-helper`       | 12px   | Badges, tooltips, helper text            |
| `--text-micro`        | 10px   | Tab bar labels, chart tick labels        |

### 4.3 Font Weights

| Variable                     | Value | Usage                              |
|------------------------------|-------|------------------------------------|
| `--font-weight-semibold`     | 600   | Headings (h1-h3)                   |
| `--font-weight-medium`       | 500   | Labels, buttons, h4, active states |
| `--font-weight-regular`      | 400   | Body text, captions, inputs        |

### 4.4 The `typo` Helper Object

All component typography MUST use the shared `typo` helper from
`/src/app/components/data/typo.ts` applied via inline `style`:

```tsx
import { typo } from '../data/typo';

// Apply typography via inline style -- never use Tailwind text-* or font-* classes
<h2 style={typo.h2}>Section Title</h2>
<p style={typo.base}>Body text here.</p>
<span style={typo.caption}>Metadata</span>
<code style={typo.mono}>console.log()</code>
```

Available `typo` keys: `h1`, `display`, `h2`, `h3`, `h4`, `base`, `label`,
`labelRegular`, `caption`, `helper`, `micro`, `mono`.

Each key maps to an object with `fontSize`, `fontWeight`, `fontFamily`, and
`lineHeight` -- all referencing CSS variables.

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
- **Never** add `font-family` to individual components -- it comes from the
  base `body` rule and the `typo` helper.

---

## 5. Component Patterns

### 5.1 shadcn/ui Primitives

The project uses the full shadcn/ui component library. Always prefer these
over custom implementations:

| Primitive      | Import from            | Notes                                       |
|----------------|------------------------|---------------------------------------------|
| `Button`       | `../ui/button`         | Pill shape via `rounded-button`. Variants: `default`, `secondary`, `outline`, `ghost`, `accent`, `destructive`, `destructive-ghost`, `link` |
| `Badge`        | `../ui/badge`          | Variants: `default`, `secondary`, `outline`, `accent`, `success`, `warning`, `destructive-subtle` |
| `Card`         | `../ui/card`           | Compound: `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter`, `CardAction` |
| `Input`        | `../ui/input`          | Uses `--radius: 8px` (`rounded-lg`)         |
| `ScrollArea`   | `../ui/scroll-area`    | Radix-based, fixes applied in `theme.css`   |
| `Tabs`         | `../ui/tabs`           | `TabsList`, `TabsTrigger`, `TabsContent`    |
| `Progress`     | `../ui/progress`       | Quality bars, loading indicators            |
| `Tooltip`      | `../ui/tooltip`        | See IconButton ref pattern below            |
| `Select`       | `../ui/select`         | Compound: `SelectTrigger`, `SelectContent`, `SelectItem` |
| `Dialog`       | `../ui/dialog`         | `rounded-card` on `DialogContent`           |
| `Separator`    | `../ui/separator`      | Section dividers                            |
| `Drawer`       | `vaul`                 | Mobile bottom sheet (iOS 26 glass material) |

### 5.2 Project-Specific UI Atoms

| Component          | Path                       | Notes                                    |
|--------------------|----------------------------|------------------------------------------|
| `IconButton`       | `../ui/icon-button`        | Plain function declaration (no forwardRef). Uses `data-slot="icon-button"` |
| `NavTab`           | `../ui/nav-tab`            | Desktop header tabs with animated indicator |
| `AnimatedIndicator`| `../ui/animated-indicator` | Motion `layoutId` pill indicator         |
| `PanelTabs`        | `../ui/panel-tabs`         | Swipeable tabs with Motion gestures for BuilderPanel |
| `PromptInput`      | `../ui/prompt-input`       | Two-mode chat composer (collapsed pill / expanded multi-line) with 44pt touch targets |
| `PromptPlusMenu`   | `../ui/prompt-plus-menu`   | Popover from "+" button for toggling features |
| `PromptToolbar`    | `../ui/prompt-toolbar`     | Chip row inside expanded composer         |
| `Queue`            | `../ui/queue`              | AI Elements-inspired progressive task list |
| `Reasoning`        | `../ui/reasoning`          | Collapsible thinking/reasoning block      |
| `Streamdown`       | `../ui/streamdown`         | Streaming markdown renderer with typewriter effect |
| `MobileTabBar`     | `../ui/mobile-tab-bar`     | iOS 26 floating glass tab bar            |
| `ToggleSwitch`     | `../shared/ToggleSwitch`   | iOS 26 Liquid Glass toggle with spring physics |
| `LargeTitleHeader` | `../shared/LargeTitleHeader` | iOS 26 collapsing large-title header   |
| `SkillMarkdown`    | `../shared/SkillMarkdown`  | Lightweight zero-dep markdown renderer   |
| `SkillBadge`       | `../features/SkillBadge`   | Badge wrapper with `rounded-lg` override |
| `ErrorBoundary`    | `../shared/ErrorBoundary`  | React error boundary with fallback UI    |
| `SelectableCard`   | `../ui/selectable-card`    | Card with selection-mode border/cursor/highlight. `data-slot="selectable-card"` |
| `RadioOptionCard`  | `../ui/radio-option-card`  | Radio option with animated dot. `data-slot="radio-option-card"` |
| `SettingsRow`      | `../shared/SettingsRow`    | Horizontal settings row (label + desc + trailing). `data-slot="settings-row-*"` |
| `ListRow`          | `../shared/ListRow`        | Compact list row (leading + label + subtitle + trailing). `data-slot="list-row-*"` |
| `SettingsNavItem`  | `../shared/SettingsNavItem` | Settings sidebar/tab button with active state. `data-slot="settings-nav-item-*"` |
| `SectionHeader`    | `../shared/SectionHeader`  | Icon + label section header. `data-slot="section-header"` |
| `ResolvedChip`     | `../shared/ResolvedChip`   | Muted confirmation pill with optional icon. `data-slot="resolved-chip"` |

### 5.3 `cn()` Utility

Always use `cn()` (from `../ui/utils`) for class merging:

```tsx
import { cn } from '../ui/utils';

<div className={cn('base-classes', isActive && 'active-class', className)} />
```

### 5.4 `data-slot` Attributes

All shadcn/ui primitives carry `data-slot` attributes. These are used in
`theme.css` for centralised typography styling. Preserve them on all
components that use them.

---

*Sections 6-16 are identical to the root `/Guidelines.md` -- refer there for the full content on iOS 26 Liquid Glass, Motion & Animation, Environment Constraints, Dark Mode, Responsive & Mobile UX, Accessibility, Charts, SVG & Image Handling, Coding Standards, Remaining Backlog, and Anti-Patterns.*