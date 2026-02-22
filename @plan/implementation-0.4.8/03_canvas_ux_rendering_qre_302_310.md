# Group 3 — Canvas UX / Rendering (QRE-302 to QRE-310)

## Scope

This group delivers user-facing improvements across the artifact canvas experience for execution review/debugging:

- graph node readability and expanded diagnostics (`QRE-302`, `QRE-304`-`QRE-307`)
- timeline event summarization (`QRE-309`)
- final output preview rendering quality (`QRE-310`)
- duplicate bookkeeping tickets (`QRE-303`, `QRE-308`) that should not create parallel implementation streams

Repo-grounded hotspots (confirmed):

- `src/frontend/src/features/artifacts/components/ArtifactGraph.tsx`
- `src/frontend/src/features/artifacts/components/ArtifactTimeline.tsx`
- `src/frontend/src/features/artifacts/components/ArtifactPreview.tsx`
- shared artifact canvas/store composition (`ArtifactCanvas`, `useArtifactStore`)
- `GraphStepNode` and payload parsing helpers (ticket-referenced, likely shared conflict hotspot)

## Ticket Inventory

| Ticket | Title | Status | Priority | Labels | Duplicate? | Explicit blockers | Explicit blocked items |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QRE-302 | Canvas Graph: Tool name badge on step nodes | Triage | Medium | `v0.4.8`, `enhancement`, `Frontend` | No | None | None |
| QRE-303 | Canvas Graph: Token/model indicator on LLM nodes | Duplicate | Low | `v0.4.8`, `backend`, `enhancement`, `Frontend` | Yes (`duplicateOf QRE-302`) | None | None |
| QRE-304 | Canvas Graph: Code preview on REPL nodes (hover expand) | Triage | Medium | `v0.4.8`, `enhancement`, `Frontend` | No | None | None |
| QRE-305 | Canvas Graph: Error detail overlay on failed nodes | Triage | Medium | `v0.4.8`, `enhancement`, `Frontend` | No | None | None |
| QRE-306 | Canvas Graph: Trajectory thought→action→observation chain view | Triage | High | `v0.4.8`, `dspy`, `enhancement`, `Frontend` | No | None | None |
| QRE-307 | Canvas Graph: Edge elapsed time labels | Triage | Low | `v0.4.8`, `enhancement`, `Frontend` | No | None | None |
| QRE-308 | Canvas REPL: Parse Zod payload schemas to separate code/variables/output | Duplicate | Medium | `v0.4.8`, `enhancement`, `Frontend` | Yes (`duplicateOf QRE-240`) | None | None |
| QRE-309 | Canvas Timeline: Contextual event summaries using Zod schemas | Triage | Medium | `v0.4.8`, `enhancement`, `Frontend` | No | None | None |
| QRE-310 | Canvas Preview: Strongly-typed final output rendering | Triage | Low | `v0.4.8`, `enhancement`, `Frontend` | No | None | None |

## Chain of Command (Dependencies)

### Explicit Linear blockers / duplicates

- `QRE-303` is an explicit **Duplicate** of `QRE-302`.
  - **No Implementation Stream** for `QRE-303` in v0.4.8.
  - Keep listed for bookkeeping/reference only.
- `QRE-308` is an explicit **Duplicate** of `QRE-240` (outside the v0.4.8 canonical implementation path).
  - **No Implementation Stream** for `QRE-308` in v0.4.8.
  - Keep listed to prevent duplicate work on REPL payload parsing.

### Inferred prerequisites (**Assumptive Logic**)

1. **`QRE-302` should precede (or define rendering conventions for) other `GraphStepNode` enhancements (`QRE-304`, `QRE-305`, `QRE-306`)**
   - It establishes baseline compact node labeling and layout constraints in collapsed state.
   - Later expanded-state work should preserve badge/status spacing and avoid rework.

2. **`QRE-304`, `QRE-305`, and `QRE-306` should share a common expanded-node subcomponent strategy before parallel execution**
   - All likely modify `GraphStepNode.tsx` and payload extraction helpers.
   - Without a shared boundary (sections/subcomponents), merge conflict risk is high.

3. **`QRE-309` and `QRE-310` should follow a shared payload parsing helper decision**
   - Both rely on typed parsing/fallback rendering for execution payloads.
   - A common helper contract avoids duplicated Zod parsing branches and inconsistent fallback semantics.

4. **`QRE-307` is mostly independent of expanded-node UI work**
   - It primarily modifies edge construction/label formatting in `ArtifactGraph.tsx`.
   - It can run in parallel once timestamp normalization assumptions are documented.

### Sequencing rationale tied to file/module overlap

- Highest conflict hotspot: `GraphStepNode` (`QRE-302`, `QRE-304`, `QRE-305`, `QRE-306`).
- Moderate overlap: shared payload parser/types/helpers used by `QRE-304`, `QRE-305`, `QRE-306`, `QRE-309`, `QRE-310`.
- Lower overlap: `QRE-307` (edge labels in `ArtifactGraph.tsx`) unless timestamp parsing utilities are shared.
- Separate components with shared semantics: `ArtifactTimeline.tsx` (`QRE-309`) and `ArtifactPreview.tsx` (`QRE-310`) depend on consistent event/payload parsing choices, but can avoid direct file conflicts if helper boundaries are agreed.

## Technical Deep Dive (Per Ticket)

### QRE-302 — Canvas Graph: Tool name badge on step nodes

#### Technical Risks

- Tool name extraction may vary across payload shapes and event versions, causing silent fallback-heavy rendering.
- Badge layout can crowd existing node labels/status indicators and degrade readability.
- Tight node width constraints may cause overflow at different zoom levels.

#### Edge Cases

- Missing tool identifiers for some step types.
- Long tool names / namespaced identifiers requiring truncation.
- Non-tool nodes incorrectly classified as tool invocations.

#### Architectural Impact

- Establishes a clearer collapsed-node information hierarchy in the graph.
- Likely becomes the baseline visual convention other graph node enrichments must preserve.
- May motivate a shared payload extraction helper for graph node summaries.

#### Dependency Notes

- No explicit blocker.
- `QRE-303` duplicates this ticket; `QRE-302` is the canonical graph-node detail stream in this cluster.
- **Assumptive Logic:** treat as a baseline graph-node enhancement before `QRE-304`/`305`/`306`.

#### Parallelization Notes

- Can run in parallel with `QRE-307`, `QRE-309`, and `QRE-310`.
- Coordinate with `QRE-304`/`305`/`306` if all modify `GraphStepNode.tsx` simultaneously.

### QRE-303 — Canvas Graph: Token/model indicator on LLM nodes (Duplicate)

#### Technical Risks

- Main risk is planning confusion: duplicate ticket accidentally gets scheduled and creates overlapping implementation with `QRE-302`.

#### Edge Cases

- Future need for token/model visibility may exceed `QRE-302` scope; if so, create a new scoped follow-up instead of reviving the duplicate.

#### Architectural Impact

- None in v0.4.8 implementation; this is bookkeeping-only.

#### Dependency Notes

- **Explicit duplicate of `QRE-302`.**
- **No Implementation Stream** in this milestone plan.

#### Parallelization Notes

- Do not assign engineering implementation time.
- Only parallelizable as Linear hygiene/checklist cleanup (if needed).

### QRE-304 — Canvas Graph: Code preview on REPL nodes (hover expand)

#### Technical Risks

- REPL payload extraction may be inconsistent across event types or historical runs.
- Large code payloads can blow up node dimensions if truncation/overflow constraints are weak.
- Expand/hover interactions may conflict with existing node selection behavior.

#### Edge Cases

- REPL node present but code text missing/empty.
- Multi-line code with long lines requiring horizontal/vertical clipping.
- Non-UTF8 or unusual formatting characters in payload.

#### Architectural Impact

- Pushes `GraphStepNode` toward richer content sections, increasing need for subcomponentization.
- Increases coupling with payload parsing helpers shared with timeline/preview work.
- Improves graph-first debugging workflows for interpreter-heavy sessions.

#### Dependency Notes

- No explicit blocker.
- Related to `QRE-308` intent, but `QRE-308` is duplicate bookkeeping only.
- **Assumptive Logic:** implement after or alongside `QRE-302` with agreed node layout constraints.

#### Parallelization Notes

- Parallel with `QRE-305`/`QRE-306` only if `GraphStepNode` subcomponent boundaries are pre-agreed.
- Safe parallel with `QRE-307`, `QRE-309`, `QRE-310` if shared parser helpers are stable.

### QRE-305 — Canvas Graph: Error detail overlay on failed nodes

#### Technical Risks

- Error payload location may vary (`error`, `text`, nested payload fields), causing unreliable extraction.
- Large tracebacks can degrade graph layout and performance without overflow handling.
- Failure styling may conflict with existing status semantics if not aligned with the design system.

#### Edge Cases

- Failed node with no explicit error text.
- Multiline traceback formatting and indentation preservation.
- Mixed content (structured error objects + strings) requiring fallback formatting.

#### Architectural Impact

- Expands graph into a stronger triage surface, reducing pane-switching dependence.
- Reinforces the need for a consistent expanded-node content pattern (code/error/trajectory sections).
- May motivate shared formatting helpers for preformatted payload snippets.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** can proceed in the same wave as `QRE-304`/`QRE-306` after expanded-node structure is agreed.

#### Parallelization Notes

- Same file conflict profile as `QRE-304` and `QRE-306` (`GraphStepNode.tsx`).
- Safer to parallelize with `QRE-307`, `QRE-309`, `QRE-310` than with other GraphStepNode expansions.

### QRE-306 — Canvas Graph: Trajectory thought→action→observation chain view

#### Technical Risks

- Trajectory payload schema variability can make parsing fragile across runtime versions.
- Large observation content may produce noisy or oversized nodes without type-aware summarization.
- Rich rendering of trajectory data may overlap or conflict with error/REPL expanded sections.

#### Edge Cases

- Partial trajectory payloads (missing thought/action/observation fields).
- Observation content that is structured JSON, long text, or error-like payloads.
- Nodes that look trajectory-capable but lack valid data.

#### Architectural Impact

- High strategic impact: materially improves interpretability of ReAct/RLM behavior directly in graph UI.
- Likely defines the canonical trajectory visualization contract used for future graph/timeline alignment.
- Increases importance of shared typed payload parsing across graph/timeline/preview components.

#### Dependency Notes

- No explicit blocker.
- Related to `QRE-301` (live E2E tracing semantics), but not blocked by it.
- **Assumptive Logic:** implement after baseline graph-node layout (`QRE-302`) and with shared expanded-node section conventions.

#### Parallelization Notes

- High merge-conflict risk with `QRE-304`/`305` due shared `GraphStepNode` expanded rendering.
- Can be developed in parallel with `QRE-307` and likely `QRE-309` if parser contracts are frozen.

### QRE-307 — Canvas Graph: Edge elapsed time labels

#### Technical Risks

- Timestamp source/precision mismatches can produce misleading duration labels.
- Dense graphs may become visually cluttered if label styling is too prominent.
- Invalid timestamps may break edge-label generation if not defensively handled.

#### Edge Cases

- Missing timestamps on either source or target step.
- Negative durations or non-monotonic timestamps due clock/source issues.
- Very short/very long durations needing formatting thresholds (`ms` vs `s`).

#### Architectural Impact

- Improves observability signal in graph transitions without backend changes.
- Keeps timing semantics close to graph construction, which may justify a reusable duration formatting utility.
- Has lower structural impact than expanded-node content tickets.

#### Dependency Notes

- No explicit blocker.
- Related to other graph tickets but mostly independent from `GraphStepNode` expanded rendering.

#### Parallelization Notes

- Good candidate for parallel implementation with `QRE-304`/`305`/`306`.
- Coordinate only if shared timestamp normalization helpers are introduced.

### QRE-308 — Canvas REPL: Parse Zod payload schemas to separate code/variables/output (Duplicate)

#### Technical Risks

- Duplicate ticket may cause accidental redundant implementation alongside `QRE-240` and/or v0.4.8 canvas rendering work.

#### Edge Cases

- If v0.4.8 requires narrower REPL parsing work than `QRE-240`, create a new scoped ticket instead of implementing from this duplicate.

#### Architectural Impact

- None for v0.4.8 execution planning; this is duplicate bookkeeping only.

#### Dependency Notes

- **Explicit duplicate of `QRE-240`.**
- **No Implementation Stream** in v0.4.8.

#### Parallelization Notes

- Do not schedule engineering work from this ticket.

### QRE-309 — Canvas Timeline: Contextual event summaries using Zod schemas

#### Technical Risks

- Parser/schema drift can cause summary gaps or excessive fallback usage.
- Overly terse summaries may remove useful debugging context if fallback strategy is too aggressive.
- Divergent summary logic from graph/preview parsing can create inconsistent interpretations of the same event.

#### Edge Cases

- Unknown/malformed payloads requiring safe fallback summaries.
- Mixed event types in a single run (tool, REPL, trajectory, errors) with different parsing paths.
- Long text fields requiring truncation while preserving meaning.

#### Architectural Impact

- Moves timeline from generic serialization to typed semantic summaries.
- Encourages shared schema/parser exports usable across multiple artifact views.
- Improves replay/debug readability without changing the backend event schema.

#### Dependency Notes

- No explicit blocker.
- Related to `QRE-301` (event semantics), `QRE-306` (trajectory semantics), and `QRE-310` (typed output rendering).
- **Assumptive Logic:** follow a shared payload helper decision to avoid duplicated parsing logic across components.

#### Parallelization Notes

- Can run in parallel with graph-node UI tickets if parser helper interfaces are agreed.
- Coordinate with `QRE-310` if both introduce shared parsing/formatting utilities.

### QRE-310 — Canvas Preview: Strongly-typed final output rendering

#### Technical Risks

- Final-output source detection may vary across runs/event sequences and choose the wrong render path.
- Structured data rendering can become noisy or slow for nested payloads without constraints.
- Error-state handling may hide partial useful output if precedence rules are unclear.

#### Edge Cases

- Failed runs with partial output and error payloads both present.
- Structured outputs that are arrays, nested JSON objects, or mixed scalar/object formats.
- Unknown payload shapes requiring graceful fallback without crashing the preview.

#### Architectural Impact

- Improves the reliability and clarity of the terminal artifact preview surface.
- Reinforces typed parsing/fallback patterns likely shared with `QRE-309`.
- May introduce reusable structured-data preview helpers beyond this milestone.

#### Dependency Notes

- No explicit blocker.
- Related to `QRE-309` and duplicate `QRE-308` intent.
- **Assumptive Logic:** sequence after parser helper conventions are defined (shared with timeline/graph payload extraction).

#### Parallelization Notes

- Can run in parallel with graph-specific tickets (`QRE-302`, `QRE-304`-`QRE-307`) if shared parser helpers are stable.
- Coordinate with `QRE-309` on shared Zod parsing/fallback utilities.

## Execution Strategy for This Group

### Phase 1: Foundation

1. `QRE-302` — establish collapsed-node tool badge and graph node layout constraints
2. Duplicate bookkeeping confirmation: `QRE-303`, `QRE-308` (**No Implementation Stream**)
3. Define shared payload parsing helper strategy for graph/timeline/preview (**Assumptive Logic**; enabling step, not a separate ticket)

### Phase 2: Features

1. `QRE-304`, `QRE-305`, `QRE-306` — expanded `GraphStepNode` feature wave (REPL code, error details, trajectory chain)
2. `QRE-307` — edge elapsed time labels in `ArtifactGraph.tsx` (can run in parallel)

### Phase 3: Typed Rendering Improvements

1. `QRE-309` — timeline contextual typed summaries
2. `QRE-310` — strongly typed final output preview rendering

### Phase 4: Canvas Integration Validation

- Mixed-run smoke coverage (tool, REPL, trajectory, error, structured output cases)
- Layout/overflow checks at common zoom levels and viewport sizes
- Fallback validation for missing/unknown payloads

## Parallel Tracks (Group-Local)

### Safe parallel work

- `QRE-307` alongside any `GraphStepNode` expanded-content tickets (`QRE-304`/`305`/`306`)
- `QRE-309` alongside graph-node UI tickets after parser-helper interface is agreed
- `QRE-310` alongside `QRE-307` if shared formatting helpers are not in flux

### Parallel with coordination

- `QRE-304`, `QRE-305`, `QRE-306`
  - Shared file risk: `GraphStepNode.tsx`
  - Coordination rule: split node expanded UI into stable sub-sections/components before parallel edits
- `QRE-309` + `QRE-310`
  - Shared risk: payload parsing/fallback utilities
  - Coordination rule: define parser helper API and fallback semantics first
- `QRE-302` + `QRE-304`/`305`/`306`
  - Shared risk: graph node layout spacing (collapsed vs expanded states)
  - Coordination rule: lock node spacing tokens/regions before parallel implementation

### Unsafe / not recommended parallelism

- Uncoordinated implementation from duplicate tickets `QRE-303` or `QRE-308`
- Simultaneous broad refactors of payload parsing helpers while `QRE-304`/`305`/`306`/`309`/`310` are landing

## Integration / Handoff Points

- **To Group 2 (RLM Assessment):** `QRE-301` validates real event/trajectory payloads that these canvas features depend on for readability.
- **To Group 5 (Telemetry/Settings):** minimal direct dependency, but shared frontend release validation should include canvas behavior under normal telemetry defaults/disabled states to ensure no instrumentation side effects on UI rendering.
- **To milestone README:** this group provides the largest frontend parallelization surface, but also the highest intra-group merge-conflict risk around `GraphStepNode` and shared parsers.

## Validation Checklist

- [ ] All tickets `QRE-302` through `QRE-310` are represented, including duplicates.
- [ ] `QRE-303` and `QRE-308` are explicitly marked **No Implementation Stream**.
- [ ] `GraphStepNode` merge-conflict hotspot is documented for `QRE-304`/`305`/`306`.
- [ ] `QRE-309` / `QRE-310` dependency on shared parser-helper decisions is marked as **Assumptive Logic**.
- [ ] Execution phases distinguish graph-node work, edge-label work, and typed timeline/preview rendering work.
- [ ] Group-local parallel track guidance includes safe, coordinated, and unsafe cases.
