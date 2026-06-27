# Product

## Register

product

## Users

Developers and AI engineers who interact with fleet-rlm through a chat-first Web UI (Workbench). They use it to delegate long-context tasks to a recursive DSPy agent, inspect reasoning and tool use, browse sandbox volumes, and optimize agent modules. Users are technically sophisticated — they care about observability, execution traces, and model behavior — not abstract marketing promises.

## Product Purpose

fleet-rlm is a persistent Daytona-backed recursive DSPy workbench. Give it a task and optional context (files, URLs, pasted text, repository refs, datasets, or prior session state), and it adapts between direct reasoning, tool use, sandboxed execution, recursive sub-task delegation, and offline prompt optimization. The product surfaces are Workbench (primary), Volumes (durable storage), and Settings (diagnostics).

Success means: the user can delegate a complex multi-step task, watch it execute with transparency, inspect results, and iterate — without hand-rolling WebSocket transport, session persistence, sandbox lifecycle, or execution traces.

## Brand Personality

Precise, technical, exploratory. The interface should feel like a professional tool for serious work — not a toy, not a demo, not a marketing page. It earns trust through transparency (every tool call, every reasoning step is visible) and through restraint (nothing decorative, nothing that wastes screen space or cognitive attention).

The chat-first interaction model is the defining UX choice: the user speaks to the system in natural language, and the system responds with visible reasoning, tool execution, and artifacts. This is not a CLI wrapper with a web skin — it's a purpose-built inspection surface for agentic execution.

## Anti-references

- **Slop generative UI**: cookie-cutter SaaS dashboards with gradient cards, hero metrics, and decorative glassmorphism. The interface earns its place; it is not filled with pattern-library defaults.
- **Cognitive overload**: competing calls-to-action, dense multi-column layouts, too many visible controls at once. Every screen should have one clear task.
- **Bad UX**: mystery-meat navigation, unlabeled controls, hidden state, unclear feedback. Agent execution is complex enough; the interface must reduce ambiguity, not add to it.

## Design Principles

1. **Transparency over magic.** Every reasoning step, tool call, and sandbox action is visible and inspectable. The user should understand what the agent did and why.
2. **Chat-first, not chat-only.** Natural language is the primary interaction mode, but the interface provides sidepanels, volume browsers, and settings surfaces for inspection and configuration.
3. **Precision through restraint.** Fewer elements, clearer hierarchy. Every pixel must earn its place. Remove before adding.
4. **Adaptive surfaces, not static layouts.** The workbench adapts to the task (reasoning, tool execution, HITL pauses). Layout changes are meaningful, not decorative.
5. **Expert tool, not beginner toy.** Optimize for power users who will spend hours in this interface. Keyboard shortcuts matter. Information density matters. Speed matters.

## Accessibility & Inclusion

- Target: WCAG 2.1 AA conformance
- All interactive elements must be keyboard-accessible
- Color is never the sole differentiator (support color blindness)
- Motion is purposeful and respects `prefers-reduced-motion`
- Clear labels, ARIA where needed, focus management for dynamic content