<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: frontend_style
  name: Terminal TUI Theme System (pi-tui + Custom Palette)
  category: frontend_style
  scope:
      - '**'
  source_files:
      - tools/fleet-tui/src/tui/theme.ts
      - tools/fleet-tui/package.json
      - tools/fleet-tui/biome.json
      - tools/fleet-tui/src/tui/command-presenter.ts
      - tools/fleet-tui/src/tui/message-renderer.ts
-->


The Fleet RLM project has no traditional web frontend; its only UI is a Node.js terminal-based TUI located under `tools/fleet-tui/`. Styling is handled entirely through a custom theme system built on top of the `@earendil-works/pi-tui` library, which renders ANSI escape sequences for colored terminal output.

**System and approach**
- The TUI is a pure TypeScript application using `tsx` for execution and `vitest` for testing.
- Visual styling is implemented via a `FleetTheme` class in `src/tui/theme.ts` that wraps ANSI color codes (`\x1b[38;2;...m` for truecolor, `\x1b[38;5;...m` for 256-color fallback) to produce styled text.
- Two built-in palettes are defined — `dark` and `light` — each mapping semantic token names (e.g. `accent`, `border`, `success`, `error`, `warning`, `text`, `mdHeading`, `syntaxKeyword`, etc.) to hex colors.
- Terminal capability detection (`getCapabilities().trueColor`) selects between truecolor and 256-color modes at runtime.

**Key files and packages**
- `tools/fleet-tui/src/tui/theme.ts` — central theme definition, palette, `FleetTheme` class, global `theme` singleton, markdown theme adapter, and select/editor theme helpers.
- `tools/fleet-tui/package.json` — declares `@earendil-works/pi-tui` (0.82.0) as the TUI framework, `highlight.js` (10.7.3) for code syntax highlighting, and Biome for formatting/linting.
- `tools/fleet-tui/biome.json` — enforces consistent code style (space indentation, 100-char line width, recommended rules with specific overrides).
- Consumers: `command-presenter.ts`, `message-renderer.ts`, `application.ts`, and others import `theme`, `selectTheme`, `editorTheme`, and `markdownTheme` from `theme.ts`.

**Architecture and conventions**
- Semantic token naming: colors are referenced by descriptive tokens (`accent`, `borderAccent`, `toolPendingBg`, `mdCodeBlock`, `syntaxFunction`, etc.) rather than raw hex values, ensuring consistency across views.
- A single active `FleetTheme` instance is created at module load and exposed via a thin `theme` object with helper methods (`fg`, `bg`, `bold`, `italic`, `underline`, `strikethrough`).
- Markdown rendering is delegated through a `MarkdownTheme` adapter that maps pi-tui's markdown callbacks to the same `theme.fg`/`theme.bold` primitives.
- Select/list UIs get their own `selectTheme` namespace (`selectedPrefix`, `selectedText`, `description`, `scrollInfo`, `noMatch`), and the editor uses `editorTheme.borderColor` plus the shared `selectTheme`.
- Status glyphs (`✓`, `!`, `×`, `…`, `·`) are centralized in `statusGlyph` for consistent visual indicators.

**Conventions and constraints**
- All terminal output must go through the `theme` helpers — direct ANSI usage is not used in consumer code.
- Palettes are immutable per scheme; switching schemes calls `setTerminalColorScheme()` which recreates the active theme.
- Color mode is auto-detected; consumers do not choose between truecolor and 256-color directly.
- Code style is enforced by Biome with a fixed formatter configuration (space indent, 100-column width) and linter rules applied uniformly across `src/**/*.ts` (excluding generated files).