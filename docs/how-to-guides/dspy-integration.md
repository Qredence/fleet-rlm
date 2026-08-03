# DSPy RLM and Daytona integration

Fleet executes every primary Turn through a fresh native `dspy.RLM`. The Root
Model generates iterative Python, while the Sub Model answers `llm_query()` and
ordered `llm_query_batched()` calls. The Root may also call `rlm_query(prompt)`
to solve one bounded semantic subproblem with a fresh child `dspy.RLM`. Both model roles are host-configured; API
clients cannot provide models, Signatures, or executable capabilities.

## Execution contract

- One Run owns one Code-Interpreter Context. Variables, imports, and functions
  persist across RLM iterations in that Run only.
- `rlm_query(prompt)` is the only recursive primitive. The Root should keep large
  input-specific data in REPL variables, select the smallest sufficient slice,
  and pass only that slice to the child. The child returns one concise typed
  `answer`; the parent retains authority over the public Signature and final
  `SUBMIT`.
- Child RLMs use fresh interpreter contexts in the same leased Daytona Sandbox,
  have no Fleet history/Workspace/Attachment/Artifact/Lakebase host Tools, and
  are bounded by `rlm.recursion_*` policy. A retained Sandbox and its mounted
  Volume are compute state, not a cross-Turn Python state store.
- A later Run receives a fresh context, even when it reuses the same Sandbox.
- Host capabilities enter the Turn blueprint as explicit `dspy.Tool` objects.
  Fleet preserves schema validation at the callable boundary used by DSPy's
  interpreter and exposes only host-approved bounded event views.
- `SUBMIT(...)` validates the active Signature and produces the typed
  `dspy.Prediction`. Fleet projects that Prediction into chat text, an optional
  structured result, and a commit-gated private `result.json` snapshot.
- Session Workspace files are immediate private Volume state. They survive
  later Runs and Sandbox replacement; interpreter globals do not.
- Daytona Workspace Memory is separate workspace-wide immediate state at the
  fixed root `MEMORIES.md` of the already mounted
  `workspaces/<workspace_id>` Volume subpath. The RLM recalls it on demand with
  `read_workspace_memory`; Fleet does not inject it at Turn start.
- `update_workspace_memory` appends one complete UTC-timestamped record, limited
  to 4 KiB of formatted UTF-8, only when the user explicitly requests memory. A
  completed append is immediately durable outside Turn Commit, so it survives
  failed or cancelled Turns and Sandbox replacement. The explicit-request rule
  is Tool audit policy, not a filesystem ACL, because the Daytona interpreter
  can see the mounted Volume. Append serialization is process-local; separate
  Fleet processes are not coordinated, so concurrent cross-process append is
  not guaranteed.
- Memory reads return the newest complete records up to 256 KiB.
  `max_upload_bytes` caps the total file; appends against full or torn state and
  access to unsafe or invalid storage fail closed without automatic compaction,
  deletion, or repair. Generic Tool events expose metadata only, never the
  learning body, provider path, or raw error; there is no dedicated memory
  event.
- Fleet scopes the stock `dspy.JSONAdapter()` to each Turn alongside the Root
  Model. Provider-native token streams and sectioned text are not reinterpreted
  as RLM actions: malformed responses remain bounded `adapter_parse_error`
  failures, which keeps the pinned DSPy protocol authoritative without changing
  process-global DSPy settings.
- The Daytona profiles use the Databricks AI Gateway model
  `deepseek-v4-flash` for both Root actions and bounded Sub Model queries.
  Fleet sends the non-secret provider route
  `Databricks-Model-Provider-Service: uscentral.default.zencode-oai` and keeps
  the credential in `DATABRICKS_TOKEN`. The standard profile uses
  `reasoning_effort = "low"` for Root and `reasoning_effort = "none"`,
  `temperature = 0` for Sub. Both responses are capped at 8,000 tokens. This
  LM response limit is distinct
  from `dspy.RLM.max_output_chars`, which bounds REPL output retained in
  recursive history.
- Fleet remains on DSPy's public program and LM call surfaces: `rlm.acall()`
  delegates request and response normalization to stock DSPy. Application code
  does not call LM `forward()` methods, construct provider-shaped requests, or
  opt into DSPy's experimental typed LM API while `dspy==3.3.0b1` is pinned.
  See DSPy's
  [normalized LM API migration](https://dspy.ai/community/normalized-lm-api-migration/).
- Do not replace the Turn-scoped adapter with global `dspy.configure()`.
  Composition may execute independent Turns with different scoped models, and
  Fleet must not mutate shared DSPy defaults.

## Native DSPy streaming

The live Root RLM uses DSPy's native streaming utilities. Fleet wraps each fresh
`dspy.RLM` with `dspy.streamify` and registers reusable
`dspy.streaming.StreamListener` instances against the named `generate_action`
predictor for its `reasoning` and `code` fields. DSPy emits
`dspy.streaming.StreamResponse` values through its async side channel before the
final typed `dspy.Prediction`.

Fleet converts those private DSPy values into the existing transport-neutral
`RLMReasoning`, `RLMCode`, and `Status` Runtime Events. The existing
`AISDKUIProjector` then emits AI SDK UI v1 SSE chunks, and `fleet-tui` appends
delta chunks to stable step cards. DSPy semantic subcalls are not streamed to
operators. `dspy.streaming_response` is deliberately not used because it would
create a second OpenAI-compatible SSE protocol; Fleet already owns its public
FastAPI SSE contract.

When the provider or cache produces no field chunks, the final typed Prediction
still completes and Fleet records an internal provider-fallback mode. This is a
normal completion path, not evidence that token streaming occurred.

## Recursive harness limits

The default policy allows two recursive levels, four child calls per Turn, a
50,000-character delegated prompt bound, eight child iterations, twelve child
LM calls, and 4,000 child output characters. The depth cap falls back to one
plain Sub Model query so recursion cannot grow without bound. Child prompts and
answers are never copied into public Runtime Events. Under the committed `safe`
MLflow policy, traces retain only depth, counts, character bounds, duration,
termination mode, and failure category. An explicitly selected local `debug`
profile may retain bounded LM previews for diagnostics, including recursive
child calls.

## Typed startup inputs

Fleet keeps domain dataclasses authoritative and validates the bounded
model-visible payload once at the `rlm.inputs` boundary immediately before
`rlm.acall()`. The default `FleetRLMSignature` describes that payload with
strict Pydantic DTOs. Skill instructions, resource bodies, Attachment bytes,
provider paths, and older history remain behind host-mediated Tools.

Custom Skill Signatures keep their existing JSON-compatible common input
annotations and continue to own only their declared output fields. Custom DSPy
Modules are outside the supported Turn composition contract; use the native RLM
with host-mediated Tools and typed inputs.

See [backend architecture](../architecture.md) for ownership and Turn commit ordering.

## Run the complete Daytona proof

The release proof is an explicitly invoked, policy-gated command that uses the
real FastAPI, DSPy, and Daytona path. `runtime.live_enabled` is true by
default; set it to `false` in `config/fleet.toml` to fail closed. Export
credentials in the invoking shell or keep them in the repository `.env`; never
place them in the repository or pass them through Fleet API requests.

```bash
# Credentials may come from the process environment or repo `.env`
# (loaded via python-dotenv; existing exports win).
uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json
```

Select `[config] default_profile = "daytona"` and restart Fleet first. Provision
the immutable Snapshot named by that profile with the
[Daytona Snapshot guide](daytona-snapshot.md).

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
