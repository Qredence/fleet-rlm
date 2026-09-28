# Book-scale RLM implementation plan

**Status:** proposed; implementation and provider validation have not started.

**Goal:** let Fleet investigate a book or larger corpus through the native DSPy
RLM loop, with source data in the Daytona filesystem, bounded REPL observations,
auditable coverage, and honest answers when evidence is incomplete. Grow input
size without assuming that a large prompt window or a single long Turn proves
correctness.

**Source of truth:** `src/fleet_rlm/` and its tests. Existing plans and docs
explain intent but do not establish current behavior.

## Current constraints to design around

- `rlm/program.py` constructs native `dspy.RLM` and gives it a Daytona-backed
  interpreter. Its attachment context capsule currently reads each staged
  attachment fully into memory, checks size and SHA-256, then puts the body in
  the REPL `attachments` value. This is the specific eager-loading path to
  change for larger inputs.
- `workspace/storage.py` caps a Workspace file at 10,000,000 bytes and a text
  read at 10,000 characters. `config/settings.py` defaults uploads to 10 MiB.
  These are separate limits; increasing only one does not create a large-source
  path.
- The bundled `long-context` Skill already describes sparse, exhaustive, and
  dependent analysis, bounded excerpts, `llm_query_batched`, and provenance.
  Reuse it as agent strategy. It is guidance, not a coverage guarantee.
- Session task checkpoints are bounded summaries. Workspace files and Artifacts
  are the durable homes for large source material and detailed result ledgers.
  A Turn remains finite and DSPy owns its REPL iterations.

## Decisions and boundaries

1. **Two milestones.** First certify one UTF-8 book that fits current storage
   limits. Then implement and certify sources beyond those limits. Do not make a
   large-upload change the prerequisite for learning whether the analysis is
   reliable.
2. **One reasoning owner.** Native `dspy.RLM` remains the only root loop. Python
   in Daytona performs deterministic partitioning, search, counting, and
   validation. Use `llm_query`/`llm_query_batched` for bounded semantic
   judgments; use an opt-in Fleet child only for a genuinely independent
   iterative investigation. No planner, vector database, mandatory RAG
   pipeline, or second execution scheduler is introduced by this plan.
3. **Define the claim.** Fleet can mechanically verify source integrity,
   partition coverage, exact quotes, and deterministic counts. It cannot
   guarantee every semantic interpretation of arbitrary books. Unresolved
   ambiguity, failed partitions, and source extraction uncertainty must be
   reported with the answer.
4. **Start with text.** UTF-8 text and Markdown are the initial certified
   formats. PDF, EPUB, and OCR need separate extraction and page-mapping
   acceptance tests before they can receive the same coverage claim.
5. **Keep authority in place.** Session and Workspace control source access;
   Daytona runs model-authored Python; FastAPI validates input and projects
   events; settlement commits results. MLflow remains observation only.
   Preserve existing Turn budgets, cancellation, cleanup, and final-output
   bounds.

## Phase 0 — Freeze a measurable book-scale benchmark

**Owner:** evaluation fixtures and harness under `tests/` and existing
evaluation scripts. No production behavior change.

- [ ] Inventory existing long-context fixtures, evaluation scripts, and
  attachment tests. Record the exact branch, configuration, model, source
  checksum, and current limits used for the baseline.
- [ ] Add a redistributable UTF-8 book-scale fixture within the current file
  limit. Preserve its original bytes. Record a manifest with source ID, SHA-256,
  byte length, encoding, ordered section IDs, and byte/character boundaries.
  Do not create a duplicate fixture if an existing one meets these needs.
- [ ] Define at least three task classes: sparse fact retrieval with a
  distractor, exhaustive extraction/counting across all sections, and a
  cross-section question whose answer depends on an amendment or exception.
  Include absent-evidence and conflicting-evidence cases.
- [ ] Write an oracle for objective assertions: required sections, expected
  records/counts, allowed exact source spans, and expected abstention or gap
  behavior. Keep interpretive judgments in a separate reviewed rubric.
- [ ] Capture baseline Turn outcome, answered/omitted facts, unsupported
  claims, citation validity, section coverage, root/sub-LM calls, tokens,
  elapsed time, final-output size, and cleanup. Report repeated runs and their
  spread, not one successful example.

**Gate:** the fixture and oracle can detect skipped middle/end sections,
fabricated quotes, a wrong count, and a falsely complete answer before any
large-source changes are accepted. Local scripted runs prove the harness;
live Daytona/model runs require the documented operator authorization.

## Phase 1 — Make coverage and provenance checkable

**Owners:** `src/fleet_rlm/skills/bundled/long-context/` for strategy;
`rlm/program.py` and the existing finalization/settlement owner for any
minimal runtime contract; Workspace files for detailed evidence. Prefer a
small reusable validation function over a new service.

- [ ] Define a versioned, compact *analysis record* in a Workspace file:
  source ID and checksum, partition ID and byte/character range, status
  (`pending`, `processed`, `failed`), extracted finding IDs, and evidence
  spans. A source revision changes the identity of its derived record.
- [ ] Specify coverage by task type. Sparse work may stop after a justified
  search and report its search scope. Exhaustive work enumerates every
  required partition before processing and reaches `processed = required`
  before saying the result is complete. Dependent work records prerequisite
  evidence before deriving downstream findings. Ranking may change order,
  never silently remove required partitions.
- [ ] Add deterministic validation of records: every cited span is within the
  identified source revision; quoted text matches original bytes or decoded
  characters under a declared normalization rule; no duplicate finding is
  counted twice; failed/pending partitions remain visible. Check boundaries
  involving multibyte UTF-8 and overlapping chunks.
- [ ] Update the bundled Skill and its resources with the smallest executable
  example for partition ledger, batched semantic extraction, verification,
  and compact `SUBMIT`. Do not move workflow ownership into a host research
  service. Reuse the existing chunking/ranking helpers where appropriate.
- [ ] At finalization, make completeness claims conditional on the validated
  record when a Turn declares an exhaustive analysis. A missing or invalid
  record must result in an explicit incomplete status or a correction request,
  not a successful “complete” claim. Keep ordinary short answers unaffected.
  Design the trigger and data shape against current typed `SUBMIT` and
  settlement contracts before coding.
- [ ] Add focused tests in behavior-owning suites for source mutation,
  bad offsets/quotes, gaps, duplicate counts, interrupted processing, and
  correct incomplete reporting. Include a full native-DSPy interpreter path;
  a prompt-only test is insufficient for the runtime claim.

**Gate:** for the exhaustive benchmark, every required partition is accounted
for, every accepted quote matches its source revision, and an interrupted or
corrupt run cannot settle a complete result. The semantic rubric is reported
separately; mechanical checks do not certify interpretation.

## Phase 2 — Add a lazy, immutable large-source path

**Owners:** Workspace upload/storage and attachment lifecycle for durable
bytes; `rlm/program.py` for context binding; Daytona runtime/interpreter for
mount and access. FastAPI owns any changed request/response contract.

- [ ] Trace the existing upload, staging, Volume mount, integrity check, and
  attachment cleanup path. Write down where the current 10 MiB and
  10,000,000-byte limits apply and the peak memory copy count. Choose an
  initial larger target from measured user cases and benchmark sizes; retain
  the existing default until the new path is tested.
- [ ] Store each source once under Session/Workspace authority with an
  immutable identity (checksum, size, encoding, source revision). Validate
  upload incrementally with bounded memory. Reject changed bytes or metadata
  before the RLM sees a source; reject symlink/path escapes and foreign
  Workspace references under the existing authority rules.
- [ ] Change the attachment context capsule so a large attachment exposes
  metadata and an authorized sandbox file path/handle to the REPL, not its
  entire decoded body. Keep small attachment behavior compatible where useful.
  Verify bytes with streaming SHA-256 and size checks before use; define how
  stale or replaced files fail. Avoid serializing book text into root LM calls,
  REPL feedback, events, or traces.
- [ ] Provide bounded Python inspection in the existing Daytona REPL: stat,
  streaming scan, byte-safe slices or decoded text pages, and section
  iteration. Prefer ordinary file operations and existing Workspace tools to
  a new host API. Preserve stable offsets and source IDs in observations.
- [ ] Set explicit per-file, total-source, chunk, and temporary-disk limits in
  `config/fleet.toml`/settings only after measuring the target. Enforce the
  same effective limits at HTTP admission, storage, staging, and sandbox
  access. Test cancellation and cleanup during an incomplete upload/read.
- [ ] Update OpenAPI and generated TUI HTTP types only if the external
  contract changes; use `make api-sync` and `make api-check`. Add attachment
  lifecycle, cross-Session isolation, memory-pressure, and integrity tests in
  existing suites. Keep the default `daytona-native` profile supported.

**Gate:** a source larger than the old limit can be staged, inspected in
bounded slices, and analyzed without materializing its whole body in Python
or an LM prompt. A checksum mismatch, modified source, path escape, or
interrupted upload fails closed and leaves no falsely complete Turn.

## Phase 3 — Support work beyond one Turn and answers beyond inline output

**Owners:** existing Workspace files, Session task checkpoint, Artifacts,
Turn settlement, and API/TUI projection. No persistent REPL process is
required for durability.

- [ ] Persist the source manifest, partition ledger, and verified findings as
  Workspace files with version/checksum. Put only a small goal, progress,
  revision, and paths in the Session task checkpoint. Define an atomic
  write/rename or equivalent commit order so a partial write is never treated
  as verified progress.
- [ ] On a later Turn, reload the files under the same Session authority,
  recheck source revisions and ledger integrity, and continue only pending or
  failed partitions. Never reuse a finding after its source revision changes.
  Provider retries are DSPy-owned and are no longer charged to the Turn budget,
  so bound repeated work with the Turn deadline and Tool-call ceilings instead.
- [ ] Decide explicitly whether product behavior is **user-driven continuation**
  across Turns (initial implementation) or an automatically continued request.
  The latter needs a separate lifecycle/API design and must not wrap DSPy in
  a hidden outer reasoning loop. A timeout remains an incomplete result until
  a later authorized Turn continues it.
- [ ] For a long report, write the complete result to an Artifact or ordered
  Workspace files, then `SUBMIT` a bounded summary with references and a
  completeness statement. Validate artifact existence, size, checksum, and
  retention before settlement. Define multi-part manifests only if one
  Artifact cannot meet a measured case.
- [ ] Exercise TUI display and retrieval of partial progress, final summary,
  Artifact references, and resumed results. Regenerate stream fixtures/types
  with `make stream-sync`/`make stream-check` only if event shapes change.

**Gate:** an interrupted multi-Turn exhaustive job resumes from committed
partitions without duplicate counting; changed source bytes invalidate its
old findings; a report longer than the inline cap remains retrievable while
the final `SUBMIT` stays within its independent output bound.

## Phase 4 — Evaluate and tune the RLM strategy

**Owners:** evaluation harness, `long-context` Skill, and existing RLM
configuration. Change one policy or strategy at a time.

- [ ] Run the same frozen corpus/tasks through the current and proposed paths.
  Measure quality separately from root-LM latency, REPL time, sub-LM time,
  input/output tokens, generated-output size, provider cost when available,
  and provider/Daytona failures. Record trace IDs and exact configuration.
- [ ] Compare sparse search, exhaustive scan, and dependent reconciliation.
  Tune chunk boundaries, evidence payloads, and `llm_query_batched` use only
  where the trace shows a benefit. Root REPL calls are sequential; favor
  compact observations and early `SUBMIT` over trying to parallelize them.
- [ ] Define acceptance thresholds before looking at candidate live results:
  objective coverage and quote checks must pass on every certified case;
  semantic rubric threshold, latency, and spend limits must be named for the
  target model and corpus. Record failures and abstentions, not only successes.
- [ ] Run the focused unit/contract lanes, `uv run ruff check`,
  `uv run ruff format --check`, `uv run ty check src` for changed interfaces,
  generated-contract checks if applicable, dependency-boundary checks for
  moved ownership, and `make check` for cross-cutting lifecycle changes.
  Live Daytona/model certification is a separately authorized gate and must
  include provider behavior, settlement, and cleanup evidence.

**Gate:** publish the supported corpus size, format, benchmark quality,
latency/cost range, and known failure modes. “Essentially unbounded” means
finite bounded processing can be continued over durable data; it is not a
claim of infinite resources, guaranteed completion time, or universal semantic
correctness.

## Delivery order and review points

`Phase 0 -> Phase 1 -> Phase 2 -> Phase 3 -> Phase 4` is the default order.
Phase 1 can deliver useful book-scale correctness work before any upload-limit
increase. Each phase should review the actual diff, delete superseded code
instead of adding a forwarding layer, update architecture guidance only when
an owner/trust boundary changes, and run `git diff --check`. Do not treat local
tests as live provider certification.

**Questions to settle before Phase 2 or an automatic continuation design:**
What first source size and formats matter in production? What latency/cost
budget is acceptable for an exhaustive book task? Should one user request
automatically span multiple Turns, or should the user explicitly continue?
These choices affect limits and API behavior; Phases 0–1 can proceed with
existing defaults.
