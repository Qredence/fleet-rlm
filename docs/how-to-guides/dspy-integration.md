# DSPy RLM and Daytona integration

Fleet executes every primary Turn through a fresh native `dspy.RLM`. The Root
Model generates iterative Python, while the Sub Model answers `llm_query()` and
ordered `llm_query_batched()` calls. Only the `daytona-recursive` policy also
lets the Root call `rlm_query(prompt=prompt)` for one bounded subproblem through
a fresh child `dspy.RLM`. Both model roles are host-configured; API clients cannot
provide models, Signatures, or executable capabilities.

## Execution contract

- One Run owns one Code-Interpreter Context. Variables, imports, and functions
  persist across RLM iterations in that Run only.
- `rlm_query(prompt=prompt)` is the only recursive primitive and is absent from the
  normal `daytona` policy. Under `daytona-recursive`, Root code keeps large
  input-specific data in REPL variables and passes only the smallest sufficient
  slice to a child; the parent retains authority over public output and final
  `SUBMIT`.
- A real child uses a dedicated, disposable Daytona Sandbox with ordinary
  Daytona network policy. It mounts the same Volume ID only at the private
  sibling scope `recursive/<workspace-id>/<run-id>/<call-index>`, never at the
  Root's `workspaces/<workspace-id>` scope. The child receives no Fleet
  history, Workspace, Attachment, Artifact, Skill, broker, or credential
  capability. Its scope is purged and its Sandbox deleted before a successful
  Root Turn can commit.
- A later Run receives a fresh context, even when it reuses the same Sandbox.
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
  with `read_workspace_memory` or `list_memories`; every Turn also receives a
  bounded, tolerant <= 4 KiB `workspace_memory tail` digest of the newest
  records inside `session_context` (cached per Volume root for 30 s).
- `remember` (or its back-compat alias `update_workspace_memory`) appends one
  complete UTC-timestamped v2 record (`<!-- id:8hex -->`), limited to 4 KiB of
  formatted UTF-8, only when the user explicitly requests memory. Repeating
  the same record is idempotent. A completed append is immediately durable
  outside Turn Commit, so it survives failed or cancelled Turns and Sandbox
  replacement. v1 rows derive a deterministic id when listed and upgrade to v2
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
- All committed profiles use `deepseek-v4-flash` for both Root actions and
  bounded Sub Model queries. The interactive `daytona` and
  `daytona-recursive` profiles use the OpenCode Go gateway, disable LM caching,
  and cap both roles at 16,000 tokens. Managed and benchmark profiles use the
  Databricks AI Gateway and cap both roles at 8,000 tokens. The inherited
  provider route is `Databricks-Model-Provider-Service:
  uscentral.default.zencode-oai`; the exact credential and endpoint names are
  policy-derived in [the profile matrix](../reference/profile-matrix.md). This
  LM response limit is distinct from `dspy.RLM.max_output_chars`, which bounds
  REPL output retained in recursive history.
- Fleet remains on DSPy's public program and LM call surfaces:
  `rlm.acall(interpreter, ...)` delegates request and response normalization to
  stock DSPy. Application code does not call LM `forward()` methods, construct
  provider-shaped requests, or opt into DSPy's experimental typed LM API while
  the supported DSPy 3.3.x line is selected. The current lock resolves 3.3.0.
  See DSPy's
  [normalized LM API migration](https://dspy.ai/community/normalized-lm-api-migration/).
- Do not replace the Turn-scoped adapter with global `dspy.configure()`.
  Composition may execute independent Turns with different scoped models, and
  Fleet must not mutate shared DSPy defaults.

## DSPy 3.3.x ownership contract

Fleet keeps the public configuration key `max_iterations`. The adapter in
`rlm.dspy_contract` maps that key to DSPy 3.3.x's constructor parameter
`max_iters`; no other Fleet configuration or public API uses the DSPy spelling.
Native RLM construction does not accept an interpreter. It installs a
fail-closed `interpreter_factory` so an invocation without a caller-owned
interpreter becomes a bounded `RLMConfigError` rather than silently creating a
DSPy interpreter.

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

## Recursive harness limits

`[defaults.rlm] recursion_enabled = false`, so normal `daytona` exposes no
recursive Tool or instruction. `[profiles.daytona-recursive.rlm]` enables the
native child path with two levels, four child calls per Turn, a 50,000-character
delegated prompt bound, eight child iterations, twelve child LM calls, and
4,000 child output characters. At depth two, DSPy uses one bounded plain Sub
Model query instead of creating a grandchild Sandbox.

Child prompts, answers, reasoning, generated code, and provider responses are
never copied into public Runtime Events. Root traces retain the normal bounded
readable preview policy, while child traces retain structural metadata only:
role, model, call index, key/count metadata, usage, duration, failure category,
and termination mode.

## Delegation lanes

Fleet exposes two bounded ways for the RLM to delegate work to a smaller model.

The default lane is DSPy's native sub-LM: `llm_query(prompt)` for one bounded
semantic judgment or `llm_query_batched(prompts)` for independent judgments in
one round trip (`rlm/signature.py` guidance). These run inside the Root
interpreter namespace as plain LM completions against `RLMModelBundle.sub_lm`,
so they cost one provider call and inherit the Root trust domain. That
inheritance is acceptable for prompt-only judgments because the Root's own
generated code already executes in the same Sandbox.

The opt-in isolation lane is the dedicated child Sandbox exposed as `rlm_query`
only when `[defaults.rlm] recursion_enabled = true` (the
`[profiles.daytona-recursive]` profile). Each delegation provisions its own
ephemeral Sandbox running a full native RLM, mounted at the sibling Volume
scope `recursive/<workspace>/<run>/<call-index>` with no Fleet capabilities,
credentials, history, or broker state; strict child cleanup gates Root success.
Cross-sandbox child runtimes are a Fleet feature, not something DSPy 3.3
provides, so their cost is sandbox provisioning, broker/interpreter startup,
and the child's own iteration budget — see `scripts/benchmark_daytona_lifecycle.py`
for measured spin-up numbers.

Choose the sub-LM lane for prompt-only extraction, counting, classification,
and judgment. Reserve the child lane for sub-problems that need iterative,
code-executing, file-touching lifecycles in isolation.

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
the `daytona` profile, and DeepSeek v4 Flash for both Root and Sub. Its bounded
receipt excludes Attachment content, prompts, generated code, provider
responses, trace IDs, broker addresses, and credentials. A passing canary
closes Phase 1 only; it does not promote or release the candidate.

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
clean tracked non-`main` candidate, the recursive profile, and DeepSeek v4
Flash for both model roles. Its receipt contains only candidate/dependency
identity, non-secret policy identifiers, two bounded durations, and boolean
assertions. It excludes prompts, answers, code, credentials, URLs, trace IDs,
Sandbox IDs, Volume IDs, and broker data.

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
single live pytest scenario once, and performs no automatic retry. It resolves
the DeepSeek v4 Flash Root and Sub roles from the selected TOML profile;
ambient model variables are ignored, and swapped or obsolete model pairs fail
the precondition. Its `--help` path requires no credentials.

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

`src/fleet_rlm/rlm/routing_eval.py` owns a bounded routes benchmark that measures
cost rather than inspecting private model reasoning. The curated classes are:

1. `python_native` for deterministic Python/REPL work.
2. `semantic_single` for one bounded `llm_query` judgment.
3. `semantic_batched` for independent `llm_query_batched` judgments.
4. `recursive_child` for a selected self-contained subproblem that truly needs
   iterative Python exploration.
5. `recursive_depth_fallback` for a child attempting one more delegation beyond
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
