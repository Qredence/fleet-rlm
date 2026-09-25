# DSPy RLM and Daytona integration

Fleet executes primary Turns through one compatible native `dspy.RLM` per Run.
A healthy Root Sandbox may be reused across sequential successful Turns; DSPy's
private `REPLHistory` and Turn capabilities are fresh for every invocation. The
current Sandbox adapter executes generated Python remotely with serializable
variable bindings. A Sandbox-local broker dispatches authorized Fleet tools and
DSPy's native semantic tools to the host through Daytona's authenticated preview
connection. Host callables and preview credentials stay on the host. The same
broker path serves root and child invocations.

Fresh per-Run programs and DSPy's private history ownership remain the
checked-in behavior. Live recursive execution and trace retrieval are verified
by the maintained recursive-batch canary; provider quality and capacity remain
separate validation gates.

## Execution contract

- One broker Root Sandbox owns the caller-provided Code-Interpreter
  Context. Variables, imports, and functions persist across sequential clean
  Turns while that Sandbox remains healthy. A failed,
  cancelled, timed-out, or evicted runtime is rotated; durable History and
  Volume-backed state are rehydrated, but arbitrary Python globals may be lost.
- Every Turn receives the complete committed `dspy.History` for its claimed
  Session checkpoint. It contains only canonical `{"request": ..., "answer": ...}`
  records; hidden reasoning, Tool output, and failed Turns are excluded.
- `rlm_query(task=task, inputs=inputs, context="")` and Root-only
  `rlm_query_batched(tasks=tasks)`
  are recursive primitives exposed when the selected policy enables recursion.
  Root code selects bounded authorized relative file or directory references;
  the host copies their checked contents into child-private scratch. The
  parent retains authority over public output and final `SUBMIT`.
- A native depth-1 child uses a dedicated, disposable volume-less Daytona
  Sandbox. It receives a fresh interpreter, only selected files, bounded
  context, and DSPy's native semantic Sub-LM tools. It cannot access parent
  Session files, credentials, mutable Root globals, or Fleet recursive tools.
  Declared result files are checked and persisted into the parent Run before
  child cleanup; unresolved cleanup blocks successful Root settlement. Full
  grandchildren are unavailable.
- A later Turn receives a fresh request/capability binding, output metadata,
  budget, and DSPy `REPLHistory`; it may reuse the same healthy Session
  interpreter and Sandbox after the previous Turn commits.
- Host capabilities enter the Turn blueprint as explicit `dspy.Tool` objects.
  Fleet preserves schema validation at the callable boundary used by DSPy's
  interpreter and exposes only host-approved bounded event views.
- `SUBMIT(...)` validates the active Signature and produces the typed
  `dspy.Prediction`. Fleet projects that Prediction into chat text, an optional
  structured result, and a commit-gated private `result.json` snapshot.
- Session Workspace files are immediate private Volume state. They survive
  later Runs and Sandbox replacement; interpreter globals do not. The host
  exposes list/stat/paged-read, write/append, unique-fragment edit, and
  file-or-empty-directory delete; edits/deletes are optional-checksum guarded,
  never recursive, and never follow symlinks.
- Daytona Workspace Memory is separate workspace-wide immediate state at
  `memory/MEMORIES.md` under the already mounted
  `workspaces/<workspace_id>` Volume subpath (the legacy root `MEMORIES.md`
  migrates on first open, never losing content). The RLM recalls it on demand
  with `read_workspace_memory`, `list_memories`, or `search_memories`; every Turn
  also receives a
  bounded, tolerant <= 4 KiB relevant+recent `workspace_memory tail` digest
  inside `session_context` (composed per Turn request, never query-stale
  cached).
- `remember` (or its back-compat alias `update_workspace_memory`) appends one
  complete provenance-aware v3 record (`<!-- id:8hex -->`), limited to 4 KiB of
  formatted UTF-8, only when the user explicitly requests memory. Repeating
  the same record is idempotent. A completed append is immediately durable
  outside Turn Commit, so it survives failed or cancelled Turns and Sandbox
  replacement. v1 rows derive a deterministic id when listed and upgrade to v3
  on edit; duplicate ids fail closed. `edit_memory` preserves an entry's id and
  timestamp, `forget` removes exactly one entry, and both execute one mounted
  agent read-modify-fsync-publish operation. The explicit-request rule is Tool
  audit policy, not a filesystem ACL, because the Daytona interpreter can see
  the mounted Volume. Append serialization is process-local; separate Fleet
  processes are not coordinated, so concurrent cross-process append is not
  guaranteed.
- Memory reads are tolerant: human edits that break a line are skipped with a
  bounded warning count, while writes stay strictly validated. Reads return the
  newest complete records up to 256 KiB.
  `min(max_upload_bytes, 256 KiB)` caps the total file; appends against full or
  torn state and access to unsafe or invalid storage fail closed without
  automatic compaction, deletion, or repair. Generic Tool events expose
  metadata only, never the
  learning body, provider path, or raw error; there is no dedicated memory
  event.
- Fleet scopes `FleetJSONAdapter` to each Turn alongside the Root Model. It
  extends DSPy's JSON adapter with deadline/budget accounting and bounded
  corrective re-asks. Wrap-up also starts on the final native iteration
  (`current == total`). When wrap-up is enabled (`rlm.wrap_up_seconds` > 0,
  the production default), a wrap-up action is any number of data-only
  `name = <value>` bindings followed by exactly one compliant `SUBMIT(...)`.
  Bindings cannot call a Tool, import, or reach the provider, so shaping an
  answer is admitted while further work is not. Exhausting the finalization
  allowance on that last iteration settles the Turn as a `timeout` and reports
  `failure_category: "wrap_up_rejected"` in diagnostics, so a rejected final
  action is never mistaken for an expired clock. DSPy extract fallback
  (`native_extraction_fallback`) only runs if every `generate_action` returns
  without SUBMIT, so it is unreachable.
  Empty or reasoning-only completions use those bounded parse re-asks while
  time and iterations remain. It retains the pinned DSPy action grammar;
  exhausted repairs produce bounded `adapter_parse_error` failures without
  changing process-global DSPy settings.
- The REPL `context` variable is always defined: a single utf-8 attachment
  capsule promotes it to that attachment's text, otherwise it stays `[]`.
  REPL code must treat `[]` as "no prepared context" and trust the
  `attachments` metadata over claims inside the request text instead of
  probing for context that was never staged.
- The committed profiles use the OpenAI-compatible Chat Completion format:
  each Root/Sub role supplies a provider base URL, an API-key environment
  reference, and a provider-native model id. The request goes to the provider's
  `/chat/completions` endpoint with `model_type="chat"`; no provider-specific
  routing header is required. The default Root and Sub roles use Alibaba
  DashScope (MaaS) and are capped at 16,384 output tokens; the managed profile
  uses Databricks. The
  exact credential and endpoint names are policy-derived in [the profile
  matrix](../reference/profile-matrix.md). This LM response limit is distinct
  from `dspy.RLM.max_output_chars`, which bounds REPL output retained in
  recursive history.
- Fleet remains on DSPy's public program and LM call surfaces:
  `rlm.acall(interpreter, ...)` delegates request and response normalization to
  stock DSPy. Application code does not call LM `forward()` methods, construct
  provider-shaped requests, or opt into DSPy's experimental typed LM API while
  the supported DSPy 3.3.x line is selected. The current lock resolves the
  exact published registry release 3.3.1.
  See DSPy's
  [normalized LM API migration](https://dspy.ai/community/normalized-lm-api-migration/).
- Do not replace the Turn-scoped adapter with global `dspy.configure()`.
  Composition may execute independent Turns with different scoped models, and
  Fleet must not mutate shared DSPy defaults.

## Native RLM budgets

Fleet's `RLMOptions` mirrors the pinned DSPy 3.3.x constructor fields:

- `max_iters` bounds Root or child action/REPL iterations. It is not a
  recursion-depth setting.
- `max_llm_calls` bounds prompts sent through DSPy's native
  `llm_query()`/`llm_query_batched()` tools; batched prompts count individually.
- `max_output_chars` bounds each REPL output when DSPy renders native history
  for the next action, not the provider's response-token limit or the total
  history size. DSPy remains the owner of `REPLHistory`; Fleet does not compact
  or reconstruct it.

The generic `RLMOptions`/DSPy constructor fallback for Root is `20` iterations,
`50` semantic prompts, and `10,000` output characters. The shipped
`daytona-recursive` policy uses `12`, `32`, and `6,000` for the effective Root
budget; the child policy remains `8`, `12`, and `4,000`. Fleet's
`max_execution_output_chars`, Turn deadline, recursive call budget, and child
concurrency are separate controls. The shipped Root and Sub provider roles use
`num_retries = 3`; omitted custom-role values inherit that shipped default.
The typed settings default is also `3` when the policy omits the field from
both defaults and the selected profile.
`rlm.verbose` controls host logging only;
operator-visible reasoning, code, output, and recursive status use Fleet's
Runtime Events and trajectory reconciliation.

Fleet has no configurable native `max_depth`: the Root starts at depth `0`, a
direct native child is depth `1`, and deeper recursive requests use the bounded
Sub Model instead of creating a grandchild RLM or Sandbox.

## DSPy 3.3.x ownership contract

Fleet uses DSPy 3.3.x's `max_iters` spelling end-to-end. The public
configuration key is `rlm.max_iters` (`Settings.rlm_max_iters`), and
`RLMOptions.max_iters` is passed directly to `dspy.RLM(max_iters=...)` in
`rlm.program` with no adapter or alias. Policies that still set the legacy
pre-3.3 iteration-budget key fail validation. Native RLM construction installs
a fail-closed interpreter factory so an invocation without a caller-owned
interpreter becomes a bounded `RLMConfigError` rather than silently creating a
DSPy interpreter; production execution passes the acquired interpreter to
`rlm.acall(...)`. The exact-version guard lives beside native construction in
`rlm.program`; Daytona uses DSPy's public `FinalOutput` type directly.

The pinned contract was checked against the official DSPy 3.3.1 sources on
2026-09-08: [`dspy/predict/rlm.py`](https://raw.githubusercontent.com/stanfordnlp/dspy/3.3.1/dspy/predict/rlm.py),
[`dspy/primitives/code_interpreter.py`](https://raw.githubusercontent.com/stanfordnlp/dspy/3.3.1/dspy/primitives/code_interpreter.py),
and [`dspy/primitives/sandbox_serializable.py`](https://raw.githubusercontent.com/stanfordnlp/dspy/3.3.1/dspy/primitives/sandbox_serializable.py).
Those sources define the zero-argument factory versus positional
caller-owned-interpreter split, invocation-scoped tool injection, native
`REPLHistory`, `FinalOutput`, and the `SandboxSerializable` transport hooks
used by this integration. DSPy invokes interpreter actions synchronously even
from `RLM.aforward`, so async Fleet host Tools are resolved through the
composition-owned bridge instead of leaking a coroutine into the adapter. The
rolling [DSPy RLM API](https://dspy.ai/api/modules/RLM/)
is useful for orientation, but the exact pinned source and installed
`dspy==3.3.1` remain the compatibility authority.

At execution time, Fleet passes its existing interpreter positionally:
`await rlm.acall(interpreter, **named_inputs)`. Fleet or the child lease owns
shutdown for the caller-provided interpreter; DSPy must not shut it down. The
same call is used for live and deterministic execution; operator-visible
progress comes from Fleet's interpreter, Tool, callback, and trajectory
observation boundaries rather than a second DSPy streaming protocol.
Deterministic test RLMs remain keyword-only substitutes. DSPy 3.3.x's stricter
namespace, Tool, and sub-LM response validation remains authoritative, while
Fleet preserves its existing RuntimeEvent, SSE, and TUI projections.

## Live iteration observation

Fleet does not wrap `dspy.RLM` with `dspy.streamify`, register DSPy
`StreamListener` objects, or project token-level `StreamResponse` values. The
pinned DSPy call remains one standard `await rlm.acall(interpreter, **inputs)`
per action. This avoids a second delta grammar and the producer cost of
re-entering provider streaming for repeated action prompts.

Operator-visible progress is still live: Fleet observes native DSPy callback
reasoning, generated interpreter code and output at the interpreter boundary,
and host Tool activity. After completion, the native `Prediction.trajectory`
reconciles missing or corrected observations into complete per-iteration
`RLMReasoning`, `RLMCode`, `RLMOutput`, and `Status` Runtime Events. The existing
`AISDKUIProjector` emits the AI SDK UI v1 SSE chunks, and `fleet-tui` renders the
live evidence and completed trajectory in terminal scrollback. DSPy semantic
subcalls are not token-streamed to operators.

## Interpreter corrective feedback

Fleet's interpreter gives the RLM bounded corrective feedback at the execution
boundary instead of a hard stop on recoverable mistakes. Empty or oversized
intermediate code returns a direct repair message with no backend execution, and
the model is expected to fix the action.

A repeated identical interpreter action that yields the same result is treated
as no progress: the first repeat returns one bounded repair message —
"Repeated interpreter action produced no progress. Choose a different action,
use the existing output, or call `SUBMIT`" — and only a second consecutive
identical repeat terminates the Turn with `RunNoProgressError`. Any different
action resets the counter, so the model always keeps at least one bounded
recovery step before the Turn is stopped. The Tool instructions already direct
the model never to repeat an identical interpreter action.

## Recursive harness limits

The shipped default profile is `daytona-native`, with recursion disabled.
The opt-in `daytona-recursive` profile enables Fleet child-RLM tools; native
`llm_query` / `llm_query_batched` remain the semantic delegation path. Set
`rlm.recursion_enabled = false` on a comparison profile to disable the bounded
recursive Tool and instruction. When enabled, one native child level is allowed,
with four reserved child calls per Turn, a 50,000-character delegated prompt
bound, eight child iterations, twelve child LM calls, 4,000 child output
characters, and at most four child workers concurrently. A child request beyond
`RLM_NATIVE_CHILD_DEPTH = 1` uses one bounded plain Sub Model query instead of
creating a grandchild Sandbox.

`rlm_query_batched` validates and reserves every prompt before starting work,
preserves input ordering, and uses all-or-nothing failure semantics. Fleet may
run independent siblings concurrently up to `recursion_max_parallel_children`;
the model chooses the decomposition, while Fleet controls concurrency.

Child prompts, answers, reasoning, generated code, and provider responses are
never copied into public Runtime Events. Root traces retain the normal bounded
readable preview policy, while child traces retain structural metadata only:
role, model, call index, key/count metadata, usage, duration, failure category,
and termination mode.

## Delegation lanes

Fleet exposes two bounded ways for the RLM to delegate work to a smaller model.

The default lane is DSPy's native sub-LM: `llm_query(prompt)` for one bounded
semantic judgment or `llm_query_batched(prompts)` for concurrent independent
judgments (`rlm/program.py` guidance). Each prompt is a separate LM query. These run inside the Root
interpreter namespace as plain LM completions against `RLMModelBundle.sub_lm`,
so each prompt consumes a semantic-call admission and inherits the Root trust domain. That
inheritance is acceptable for prompt-only judgments because the Root's own
generated code already executes in the same Sandbox.

The isolation lane is the dedicated child Sandbox exposed as `rlm_query` and
Root-only `rlm_query_batched` under the opt-in recursive policy. Each native
depth-1 delegation provisions an ephemeral, Volume-less SemanticChild Sandbox
running a full native RLM. It receives only the selected, host-authorized source
copy and already-loaded Skill resources; it does not receive the Session Volume,
parent tools, memory, task checkpoints, attachment storage, publication
capabilities, credentials, mutable Root state, or Root broker state. Source
size and available modification metadata are rechecked around bounded staging;
scratch is private, and declared result files are path/symlink/size validated,
harvested, and persisted in the parent
Run before child teardown. Strict child cleanup gates Root success. Child
Root/Sub DSPy runtimes are copied per sibling to isolate mutable model histories
and callback bookkeeping. The provider network-block request is not evidence of
enforcement; the Phase 5 waiver remains, and no child network restriction is
claimed as verified.
Cross-sandbox child runtimes are a Fleet feature, not something DSPy 3.3
provides, so their cost is sandbox provisioning, broker/interpreter startup,
and the child's own iteration budget — see `scripts/benchmark_daytona_lifecycle.py`
for measured spin-up numbers.

Choose the sub-LM lane for prompt-only extraction, counting, classification,
and judgment. Use native batching for independent semantic judgments. Reserve
the child lane for sub-problems that need iterative, code-executing, file-touching
lifecycles in isolation, and use recursive batching only when every independent
subproblem individually justifies a child RLM.

## Daytona broker polling and cell overhead

The host-side Daytona broker uses a pooled `httpx.Client`, one broker-owned
callback executor per runtime, and bounded long-polling on `/pending` and
streamed `/output`. A useful cell-level trace breakdown is emitted on the
`sandbox.execute` span: `poll_latency_ms`, `pending_wait_requested_ms`,
`pending_wait_elapsed_ms`, `output_poll_latency_ms`,
`output_wait_requested_ms`, `output_wait_elapsed_ms`,
`callback_dispatch_ms`, `tool_execution_ms`, `result_post_ms`, and
`execution_wall_ms`. Requested wait and observed server-side condition wait are
reported separately so a preview-proxy delay is not mistaken for broker idle
time. The output path exits as soon as the broker marks a cell complete and
performs one final release read instead of a fixed post-completion drain.

These measurements are local instrumentation and do not imply a live Daytona
SLO. Run the co-located broker tests for deterministic protocol coverage, then
run the explicit credentialed lifecycle/latency benchmarks before changing
snapshot or warm-pool policy. The current architecture already pre-warms and
reuses the resident Root Sandbox; recursive children remain isolated until
live measurements show their lifecycle exceeds the documented decision gate.

## Typed startup inputs

Fleet keeps domain dataclasses authoritative and validates the bounded
model-visible payload once at the `rlm.inputs` boundary immediately before
`rlm.acall(interpreter, ...)`. The default `FleetRLMSignature` describes that payload with
strict Pydantic DTOs. Skill instructions, resource bodies, Attachment bytes,
provider paths, and older history remain behind host-mediated Tools.

Custom Skill Signatures keep their existing JSON-compatible common input
annotations and continue to own only their declared output fields. Custom DSPy
Modules are outside the supported Turn composition contract; use the native RLM
with host-mediated Tools and typed inputs.

See the root [architecture](../../ARCHITECTURE.md) for ownership and Turn commit ordering.

## Run the Phase 1 Daytona stream canary

The Phase 1 one-Turn live canary is maintained as a pytest test. It checks that
a small text Attachment is materialized through the Volume capsule, native
`llm_query` and `llm_query_batched` calls occur without `rlm_query`, Root
reasoning or code reaches SSE before terminal completion, typed `SUBMIT`
finishes the Turn, and Turn-owned broker, Sandbox, and Volume resources clean
up. This canary explicitly requires the opt-in `daytona-recursive` profile;
the shipped default remains `daytona-native`.

For the canary, set `[config] default_profile` to `"daytona-recursive"` in
`config/fleet.toml` and set `FLEET_P27_SESSION_SNAPSHOT` to the candidate's
immutable Daytona snapshot name, then run:

```bash
FLEET_PHASE1_STREAM_EVIDENCE_PATH=.scratch/phase1-daytona-stream.json \
FLEET_P27_SESSION_SNAPSHOT=your-candidate-snapshot-v1 \
uv run pytest -q -n 0 --timeout=900 \
  tests/live/backend/test_phase1_daytona_stream.py::test_phase1_daytona_stream_through_fastapi
```

The test requires `runtime.live_enabled`, an allowed Root and Sub model, and
Daytona and model credentials. The evidence-path variable must be exported in
the process environment before pytest starts. The immutable snapshot variable
is required to admit the recursive profile for this test. The test loads
`.env` with `override=False`; operator exports retain precedence. A passing
canary is evidence for this test only and does not promote or release the
candidate. Replace the snapshot example with the candidate's immutable Daytona
snapshot name, which must end in `-v` followed by a positive integer.

## Current live Daytona contracts

The native verifier runs two current contracts against one committed candidate:
the native single and ordered batch semantic-call path through FastAPI, and the
staged Attachment / durable Artifact contract across Sandbox replacement.
It requires explicit live authorization, `runtime.live_enabled`, the
`daytona-native` profile, bounded Root and Sub model IDs, configured provider
credentials, and a clean tracked non-`main` candidate.

```bash
export FLEET_LIVE=1
export FLEET_LIVE_ROOT_MODEL="your-root-model-id"
export FLEET_LIVE_SUB_MODEL="your-sub-model-id"
uv run python scripts/live_daytona_verify.py \
  --output .scratch/live/native-daytona-run-001.json
```

The output must be a new ignored or out-of-repository path. The command records
candidate and model identity, contract assertions, bounded counts, resource
IDs, durability checksums, and cleanup facts. Its `--help` path does not load
credentials. This proves only the two named contracts; it does not exercise
recursive child execution or establish containment, promotion, release, or
deployment.

## Current recursive-batch canary

The separate recursive canary runs two native DSPy child RLMs from one ordered
Root batch, verifies observed concurrency and child trace hierarchy, reuses the
Root on a second Turn, and requires child cleanup and restored admission. It
selects the opt-in `daytona-recursive` policy through an isolated policy copy;
the shipped default remains `daytona-native`. It requires `FLEET_LIVE=1`,
enabled live policy, configured Daytona/model credentials, and a clean tracked
non-`main` candidate.

```bash
FLEET_LIVE=1 uv run python scripts/live_recursive_batch_canary.py \
  --output /tmp/fleet-rlm-recursive-batch-run-001.json
```

The receipt is a single canary result. It does not certify containment,
comparative quality, child promotion, release, or deployment. The Phase 5
network-policy waiver remains recorded in the active ledger and is not network
isolation evidence.

Both commands are operator-run live checks; they are not part of this scripts
refactor's local validation.


## Routing evaluation

`src/fleet_rlm/optimization/routing.py` owns a bounded routes benchmark that measures
cost rather than inspecting private model reasoning. The curated classes are:

1. `python_native` for deterministic Python/REPL work.
2. `semantic_single` for one bounded `llm_query` judgment.
3. `semantic_batched` for independent `llm_query_batched` judgments.
4. `recursive_child` for a selected self-contained subproblem that truly needs
   iterative Python exploration.
5. `recursive_batch` for independent subproblems where each needs iterative
   Python exploration in its own child Sandbox.

Children receive no Fleet recursion tools; further semantic work inside a
child uses native `llm_query` calls under the child budget.

The deterministic lane uses dummy models and in-process interpreters; public
Tool observations, recursive summaries, answer text, child-runtime creation
counts, and latency are the only evidence. The same `run_routing_scenario` lane
may be invoked with provider-backed caller-owned interpreters and child
runtime factories for optional live comparisons. Live runs are isolated from
normal Session persistence by construction: they do not create durable Turn
rows, and optional engineering tracing remains fail-soft/operator-owned.
`RoutingScore.routing_efficiency` is intentionally independent from final
answer correctness so an expensive recursive child cannot hide behind a
correct answer.


### Running the routing matrix

Use the offline reducer and plan receipts in normal validation, and invoke the opt-in
live lane only when the selected Fleet profile and Daytona credentials are available:

```bash
uv run python scripts/benchmarks/run_routing_eval.py   --output .scratch/p12/routing-plan.json

uv run python scripts/benchmarks/run_routing_eval.py --live --repeat 3   --timeout-seconds 1800   --output .scratch/p12/routing-live.json
```

The live runner boots a temporary SQLite database and unique Daytona Volume per
run, uses public SSE facts only, and writes answer hashes rather than model or
provider payloads. A repeated recursive-child route miss is evidence, while an
unnecessary child for a simple deterministic calculation remains a routing miss.
