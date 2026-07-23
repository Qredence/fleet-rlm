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

## Typed startup inputs

Fleet keeps domain dataclasses authoritative and validates the bounded
model-visible payload once at the `rlm.inputs` boundary immediately before
`rlm.acall()`. The default `FleetRLMSignature` describes that payload with
strict Pydantic DTOs. Skill instructions, resource bodies, Attachment bytes,
provider paths, and older history remain behind host-mediated Tools.

Custom Skill Signatures keep their existing JSON-compatible common input
annotations and continue to own only their declared output fields. A custom
DSPy Module requires the evidence gate in [ADR 0003](../adr/0003-dspy-program-composition.md).

See [backend architecture](../architecture.md) for ownership and Turn commit ordering.

## Run the complete Daytona proof

The release proof is intentionally opt-in and uses the real FastAPI, DSPy, and
Daytona path. Export credentials in the invoking shell; never place them in the
repository or pass them through Fleet API requests.

```bash
export FLEET_LIVE=1
export FLEET_CONFIG_PROFILE=daytona
export FLEET_DAYTONA_SNAPSHOT=fleet-rlm-python313-v2
# Credentials may come from the process environment or repo `.env`
# (loaded via python-dotenv; existing exports win).
uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json \
  --root-model deepseek-v4-flash-free \
  --sub-model deepseek-v4-flash-free
```

Provision the immutable Snapshot first with the [Daytona Snapshot guide](daytona-snapshot.md).

The verifier requires a clean tracked tree on a non-`main` branch, invokes the
single live pytest scenario once, and performs no automatic retry. The model
options override only the child proof process and must be supplied together;
they do not modify `.env`. When omitted, the proof defaults to the gateway-local
bare id `deepseek-v4-flash-free`; `normalize_model_id` turns that into
`openai/deepseek-v4-flash-free` for `dspy.LM`. Its `--help` path requires no
credentials.

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
