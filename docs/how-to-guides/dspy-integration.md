# DSPy RLM and Daytona integration

Fleet executes primary Turns through one compatible native `dspy.RLM` per
resident Session runtime. The caller-owned interpreter and Root Sandbox may be
reused across sequential successful Turns; DSPy's private `REPLHistory` and
Turn capabilities are fresh for every invocation. The Root Model generates
iterative Python, while the Sub Model answers `llm_query()` and ordered
`llm_query_batched()` calls. The committed Daytona policies also expose
`rlm_query(prompt=prompt)` and the Root-only ordered
`rlm_query_batched(prompts=prompts)` for isolated iterative subproblems through
bounded child `dspy.RLM` runtimes. Both model roles are host-configured; API
clients cannot provide models, Signatures, or executable capabilities.

## Execution contract

- One resident Session runtime owns one caller-provided Code-Interpreter
  Context. Variables, imports, and functions persist across sequential clean
  Turns while that compatible runtime remains healthy and resident. A failed,
  cancelled, timed-out, or evicted runtime is rotated; durable History and
  Volume-backed state are rehydrated, but arbitrary Python globals may be lost.
- Every Turn receives the complete committed `dspy.History` for its claimed
  Session checkpoint. It contains only canonical `{"request": ..., "answer": ...}`
  records; hidden reasoning, Tool output, and failed Turns are excluded.
- `rlm_query(prompt=prompt)` and Root-only `rlm_query_batched(prompts=prompts)`
  are the recursive primitives exposed by the committed Daytona policies.
  Under the selected recursive policy, Root code keeps large input-specific
  data in REPL variables and passes only the smallest sufficient slice to a
  child; the parent retains authority over public output and final `SUBMIT`.
- A native depth-1 child uses a dedicated, disposable Daytona Sandbox with
  ordinary Daytona network policy. It mounts the same Volume ID only at the
  private sibling scope `recursive/<workspace-id>/<run-id>/<call-index>`, never
  at the Root's `workspaces/<workspace-id>` scope. The child receives no
  Session/Workspace/Attachment/Artifact/Skill capability, broker, credentials,
  or mutable Root globals; it receives an immutable committed Session
  History snapshot, bounded Session metadata, its recursive `rlm_query` tool,
  and DSPy's native semantic Sub-LM tools. Its scope is purged and its Sandbox
  deleted before a successful Root Turn can commit. A child's further recursive request is a
  depth-2 Sub-LM fallback and does not create another Sandbox.
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
- Fleet scopes the stock `dspy.JSONAdapter()` to each Turn alongside the Root
  Model. Provider-native token streams and sectioned text are not reinterpreted
  as RLM actions: malformed responses remain bounded `adapter_parse_error`
  failures, which keeps the pinned DSPy protocol authoritative without changing
  process-global DSPy settings.
- The committed profiles use the OpenAI-compatible Chat Completion format:
  each Root/Sub role supplies a provider base URL, an API-key environment
  reference, and a provider-native model id. The request goes to the provider's
  `/chat/completions` endpoint with `model_type="chat"`; no provider-specific
  routing header is required. Interactive profiles cap both roles at 16,000
  tokens; managed and benchmark profiles cap them at 8,000. The exact
  credential and endpoint names are policy-derived in [the profile
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
- `max_output_chars` bounds retained REPL output/history, not the provider's
  response-token limit.

The default Root policy is `20` iterations, `50` semantic prompts, and `10,000`
retained output characters. The child policy is `8`, `12`, and `4,000`. Fleet's
`max_execution_output_chars`, Turn deadline, recursive call budget, and child
concurrency are separate controls. `rlm.verbose` controls host logging only;
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
`rlm.acall(...)`. Exact-version and FinalOutput adaptation lives in
`rlm._dspy_compat`.

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

`[defaults.rlm] recursion_enabled = true`, so the committed Daytona profiles
expose the bounded recursive Tool and instruction. The selected
`daytona-recursive` profile enables one native child level with four reserved
child calls per Turn, a 50,000-character delegated prompt bound, eight child
iterations, twelve child LM calls, 4,000 child output characters, and at most
five child workers concurrently. A child request beyond
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
semantic judgment or `llm_query_batched(prompts)` for independent judgments in
one round trip (`rlm/program.py` guidance). These run inside the Root
interpreter namespace as plain LM completions against `RLMModelBundle.sub_lm`,
so they cost one provider call and inherit the Root trust domain. That
inheritance is acceptable for prompt-only judgments because the Root's own
generated code already executes in the same Sandbox.

The isolation lane is the dedicated child Sandbox exposed as `rlm_query` and
Root-only `rlm_query_batched` under the committed recursive policy. Each
native depth-1 delegation provisions its own ephemeral Sandbox running a full
native RLM, mounted at the sibling Volume scope
`recursive/<workspace>/<run>/<call-index>` with no ordinary Fleet capabilities,
credentials, history, or broker state; strict child cleanup gates Root success.
A depth-2 delegation uses the bounded Sub-LM fallback instead. Child Root/Sub
DSPy runtimes are copied per sibling to isolate mutable model histories and
callback bookkeeping.
Cross-sandbox child runtimes are a Fleet feature, not something DSPy 3.3
provides, so their cost is sandbox provisioning, broker/interpreter startup,
and the child's own iteration budget — see `scripts/benchmark_daytona_lifecycle.py`
for measured spin-up numbers.

Choose the sub-LM lane for prompt-only extraction, counting, classification,
and judgment. Use native batching for independent semantic judgments. Reserve
the child lane for sub-problems that need iterative, code-executing, file-touching
lifecycles in isolation, and use recursive batching only when every independent
subproblem individually justifies a child RLM.

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

See [backend architecture](../architecture.md) for ownership and Turn commit ordering.

## Run the Phase 1 Daytona stream canary

Phase 1 closure uses a deliberately narrow, one-Turn live canary. It selects
the normal `[profiles.daytona]` policy—not `daytona-bench`—and proves one small
text Attachment is materialized through the Volume capsule, native
`llm_query` and `llm_query_batched` calls occur without `rlm_query`, Root
reasoning or code reaches SSE before terminal completion, typed `SUBMIT`
finishes the Turn, and the Turn-owned broker/Sandbox/Volume resources clean
up.

```bash
uv run python scripts/live_phase1_stream_verify.py \
  --output .scratch/fleet-rlm-recursive-runtime/evidence/daytona-dspy-stream-<run-id>.json
```

The command is explicitly invoked and policy-gated by
`runtime.live_enabled`. It loads `.env` with `override=False`, so operator
exports retain precedence. It requires a clean tracked non-`main` candidate,
the `daytona` profile, and the `databricks-deepseek-v4-flash-0731` endpoint for
both Root and Sub. Its bounded receipt excludes Attachment content, prompts,
generated code, provider responses, trace IDs, broker addresses, and
credentials. A passing canary closes Phase 1 only; it does not promote or
release the candidate.

## Run the Phase 2 Daytona recursive-child canary

After Phase 1 has a committed passing receipt and retrospective, the narrow
Phase 2 canary selects `[profiles.daytona-recursive]`. It proves one native
DSPy child RLM receives a dedicated Daytona Sandbox with ordinary network
policy, a sibling private Volume scope, no Root Python marker, and strict
cleanup before the Root typed `SUBMIT` completes. It does not use
`daytona-bench`, Oolong, a custom agent loop, or a grandchild Sandbox.

```bash
uv run python scripts/live_phase2_recursive_verify.py \
  --output .scratch/fleet-rlm-recursive-runtime/evidence/daytona-dspy-recursive-<run-id>.json
```

The command requires explicit live authorization, `runtime.live_enabled`, a
clean tracked non-`main` candidate, the recursive profile, and the
`databricks-deepseek-v4-flash-0731` endpoint for both model roles. Its receipt
contains only candidate/dependency identity, non-secret policy identifiers,
two bounded durations, and boolean assertions. It excludes prompts, answers,
code, credentials, URLs, trace IDs, Sandbox IDs, Volume IDs, and broker data.

## Run the complete Daytona proof

`live_daytona_verify.py` remains the broader MVP/release proof; it is not
repurposed for the Phase 1 canary. The release proof is an explicitly invoked,
policy-gated command that uses the real FastAPI, DSPy, and Daytona path.
`runtime.live_enabled` is true by
default; set it to `false` in `config/fleet.toml` to fail closed. Export
credentials in the invoking shell or keep them in the repository `.env`; never
place them in the repository or pass them through Fleet API requests.

```bash
# Credentials may come from the process environment or repo `.env`
# (loaded via python-dotenv; existing exports win).
uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json
```

Select the intended provider profile in `[config] default_profile` and restart
Fleet first. The shipped default is `daytona-recursive`; use the [profile
matrix](../reference/profile-matrix.md) to provide its environment names.
Provision the immutable Snapshot named by that profile with the [Daytona
Snapshot guide](daytona-snapshot.md).

The verifier requires a clean tracked tree on a non-`main` branch, invokes the
single live pytest scenario once, and performs no automatic retry. It resolves the `databricks-deepseek-v4-flash-0731` Root and Sub roles from
the selected TOML profile; ambient model variables are ignored, and swapped or
obsolete model pairs fail the precondition. Its `--help` path requires no
credentials.

The proof exercises a typed host Signature, state across RLM iterations,
single and batched recursive calls, a host Tool, a durable workspace write,
typed submission, result snapshot commit, strict SSE completion, Sandbox
replacement, fresh interpreter state, Session History reload, and strict
Sandbox/Volume cleanup.

The current proof does not establish Workspace Memory across real
provider-backed Sandbox replacement and separate Sessions. That live
cross-Sandbox, cross-Session proof remains gated and has not been run.

## Evidence and failure policy

The schema-versioned JSON receipt contains only the candidate fingerprint,
versions and model identifiers, timestamps, resource and correlation ids,
bounded counts, checksums, and pass/fail facts. It never retains prompts,
generated code or stdout, Tool arguments or results, Session bodies,
credentials, provider exception text, or stack traces.

Configured credential values and known secret variable names are checked in
memory against public Runtime Events, committed data, scoped Volume files,
Sandbox environment names, application logs, and the receipt. Cleanup failure
invalidates an otherwise successful proof. A failed receipt uses only one of
the closed categories `precondition_failed`, `proof_failed`, `cleanup_failed`,
`receipt_invalid`, or `interrupted` plus the bounded failed phase.

The receipt is local release evidence, not deployment authorization. Full
source-candidate promotion still requires the matching CI, local release-gate,
and human-review evidence defined by the release process.


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
6. `recursive_depth_fallback` for a child attempting one more delegation beyond
   `RLM_NATIVE_CHILD_DEPTH`; the bounded plain Sub LM answers it and no second
   child Sandbox is allocated.

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
