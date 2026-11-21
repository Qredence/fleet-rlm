# Phase 4 — Vite + React + shadcn/ui Base UI AI Elements Frontend

## Objective
Build a crisp, zero-BFF Vite + React + TypeScript frontend using **shadcn/ui (Base UI preset)** and the official **AI Elements** primitives (`Trace`, `Code Block`, `Badge`) connected directly to FastAPI's `x-vercel-ai-ui-message-stream: v1` endpoint via `@ai-sdk/react` `useChat`.

## Project Setup Commands
```bash
pnpm create vite frontend --template react-ts
cd frontend
pnpm add ai @ai-sdk/react lucide-react
pnpm dlx shadcn@latest init        # Select: Vite, Base UI preset, Tailwind v4
pnpm dlx shadcn@latest add trace code-block badge button textarea dialog scroll-area
```
Reference the official Base UI AI Elements pattern specs during implementation:
- `https://ui.shadcn.com/docs/ai-elements/base/` (Chat, Reasoning, Tool, Subagent, File Tree, Stack Trace, Confirmation).

## Files to Create
- `frontend/vite.config.ts` (dev proxy `/api` -> `http://localhost:8000` with SSE buffering disabled)
- `frontend/src/lib/api.ts` (typed fetch helpers for sessions, messages hydration, file upload/download, memories, skills)
- `frontend/src/components/agent/MessageParts.tsx` (renders every AI SDK `UIMessage.part`)
- `frontend/src/components/agent/SubagentTrace.tsx` (recursive `TraceCollapsible` rendering live `preliminary: true` child `UIMessage.parts`)
- `frontend/src/components/agent/PythonToolTrace.tsx` (`Trace` + streaming `Code Block` + stdout/error `Stack Trace`)
- `frontend/src/components/agent/WorkspaceSidebar.tsx` (Session switcher, File uploader & `File Tree`, Artifact downloads)
- `frontend/src/components/agent/MemoryDrawer.tsx` (Approve/edit/delete Neon memories, highlighting Jev pending suggestions)
- `frontend/src/components/agent/SkillPicker.tsx` (`@skill` chip selector populated from `GET /api/skills`)
- `frontend/src/App.tsx`

## Technical Specifications

### 1. Direct `useChat` Wiring (Zero Custom SSE Parser)
```tsx
const { messages, sendMessage, stop, status, error } = useChat({
  id: sessionId,
  messages: initialMessages, // Hydrated directly from GET /api/sessions/{id}/messages
  transport: new DefaultChatTransport({
    api: `/api/sessions/${sessionId}/chat`,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: () => ({ pinned_skills: pinnedSkills }),
  }),
})
```
- Clicking **Stop** calls `stop()`, which aborts the stream. FastAPI handles the disconnect and tears down the Daytona interpreter context asynchronously. The Neon lock stays held until cleanup finishes.

### 2. Part-to-Component Mapping (`MessageParts.tsx`)
Render `message.parts` strictly by `part.type`:
- `data-skills` -> Row of compact shadcn `Badge` chips showing activated skills + Jev confidence score.
- `reasoning` -> `TraceCollapsible` (auto-expanded while `state === "streaming"`, auto-collapsed when done).
- `tool-python_exec` -> `PythonToolTrace`: streams raw Python inside shadcn `Code Block` (`language="python"`), displays stdout below, and renders tracebacks cleanly on `output-error`.
- `tool-subagent` -> `SubagentTrace`: `TraceCollapsible` showing child task prompt and **recursively rendering** `part.output.parts` inside nested `Trace` lines as `preliminary: true` snapshots arrive.
- `data-file-tree` -> Compact `File Tree` listing newly created `/workspace/artifacts/...` with one-click download links to `GET /api/sessions/{id}/files/{path}`.
- `text` -> Markdown message bubble.

### 3. Vite Proxy Requirement (`vite.config.ts`)
Ensure `/api` proxies to `http://localhost:8000` without response buffering so SSE chunks flush immediately in local dev.

## Exit Gate (Must Pass Before Phase 5)
```bash
cd frontend
pnpm typecheck
pnpm build
```
- **Acceptance criteria:**
  1. Upload a CSV via `WorkspaceSidebar` -> appears immediately in `File Tree`.
  2. Submit a multi-step prompt that triggers a Skill (`Badge`), Python execution (`Code Block`), and a recursive `spawn_agent` (`SubagentTrace` with nested `Trace` steps) — all stream live with zero console errors.
  3. Hard-refresh the browser tab: `GET /api/sessions/{id}/messages` restores the exact same messages, code blocks, skill badges, and nested subagent traces from Neon `runs.ui_parts`.
  4. Clicking **Stop** mid-run aborts the stream cleanly. Submitting the next prompt briefly retries transient `409 Conflict` responses while Daytona and Neon finish cleanup, then runs the prompt.
