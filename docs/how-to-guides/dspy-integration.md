# DSPy RLM and Daytona integration

Fleet executes every primary Turn through a fresh native `dspy.RLM`. The Root
Model generates iterative Python, while the Sub Model answers `llm_query()` and
ordered `llm_query_batched()` calls. Both model roles are host-configured; API
clients cannot provide models, Signatures, or executable capabilities.

## Execution contract

- One Run owns one Code-Interpreter Context. Variables, imports, and functions
  persist across RLM iterations in that Run only.
- A later Run receives a fresh context, even when it reuses the same Sandbox.
- Host capabilities enter the Turn blueprint as explicit `dspy.Tool` objects.
  Fleet preserves schema validation at the callable boundary used by DSPy's
  interpreter and exposes only host-approved bounded event views.
- `SUBMIT(...)` validates the active Signature and produces the typed
  `dspy.Prediction`. Fleet projects that Prediction into chat text, an optional
  structured result, and a commit-gated private `result.json` snapshot.
- Session Workspace files are immediate private Volume state. They survive
  later Runs and Sandbox replacement; interpreter globals do not.

See [backend architecture](../architecture.md) for ownership and
[runtime execution flow](../explanation/agent-runtime-execution-flow.md) for
Turn commit ordering.

## Run the complete Daytona proof

The release proof is intentionally opt-in and uses the real FastAPI, DSPy, and
Daytona path. Export credentials in the invoking shell; never place them in the
repository or pass them through Fleet API requests.

```bash
export FLEET_LIVE=1
export FLEET_DAYTONA_API_KEY='...'
export FLEET_LLM_API_KEY='...'
uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json
```

The verifier requires a clean tracked tree on a non-`main` branch, invokes the
single live pytest scenario once, and performs no automatic retry. Its
`--help` path requires no credentials.

The proof exercises a typed host Signature, state across RLM iterations,
single and batched recursive calls, a host Tool, a durable workspace write,
typed submission, result snapshot commit, strict SSE completion, Sandbox
replacement, fresh interpreter state, Session History reload, and strict
Sandbox/Volume cleanup.

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
