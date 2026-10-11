# Fleet simplification verification

Validation date: 11 October 2026. Operator authorization covered existing model,
Daytona, SQL, and MLflow credentials and quota. No schema migration, dependency
upgrade, model change, prompt tuning, or recursion-depth expansion was performed.

## Implementation and baseline

The preserved working-tree baseline was commit
`e51e80d24951a184d6d2f16320ba67e2b5d757ef` plus its existing local changes.
Its patch and untracked provider modules were copied into an isolated baseline
checkout. Existing local changes remain in the primary checkout.

The [Turn walkthrough](../reference/turn-walkthrough.md) identifies concrete calls
and owners. Root recursion is mandatory; children retain depth-one isolation.
Application composition retains unresolved disposal, the preparation component
owns explicit acquisition, and DSPy invocation shutdown remains separate from
remote Sandbox retirement. Configuration metadata has one catalog; execution
event values are separate from observers; memory uses its canonical storage
contract. Historical signature probing and redundant memory constructors were
removed. Windows-path redaction covers single-letter and numeric components.

Python source was 51,854 lines in 125 files at the preserved baseline and 51,499
lines in 128 files after simplification (355 fewer lines). This comparison does
not attribute the pre-existing Daytona runtime split to this implementation.
Across composition, interpreter, execution, and memory owner modules, AST counts
of `getattr`, `hasattr`, and `inspect.signature` calls decreased from 76 to 35.
Remaining calls include external DSPy/SDK shapes and optional backend capabilities.

| Identifier | Baseline and final |
| --- | --- |
| Fleet / DSPy / Daytona / MLflow | 0.7.11 / 3.4.0 / 0.220.0 / 3.16.1 |
| Python | 3.13.13 |
| Root and sub-model | `uscentral.ai_gateway.deepseek-v4-1-flash-service` |
| Root snapshot | `fleet-rlm-python313-v10` |
| Child snapshot | `fleet-rlm-python313-child-v5` |
| Baseline TOML SHA-256 | `3e7a95fa368690cfd41112f6ab615b0fc8ea09679cd6e2f10321354e7b15e75a` |
| Final TOML SHA-256 | `6119d1202435a4af10e5ea0c2338e7235a1f39cb8d7eab9cc68be4c6d8051ac0` |

The TOML difference removes only `rlm.recursion_enabled`. Its retired spelling
is rejected with an instruction to remove it. The existing memory policy has
`autonomous_memory_categories = []`: live memory checks exercised explicit
`remember` and checkpoint persistence. Commit-gated candidate promotion is
covered by the deterministic walkthrough and promotion/recovery suites.

## Commands and deterministic results

Commands were run from the repository root unless indicated otherwise:

```sh
make config-reference
uv run pytest tests/sessions/test_turn_coordinator_execution.py
uv run pytest tests/sessions/test_live_composition.py
uv run pytest tests/daytona
make check
make check-docs
git diff --check
uv run fleet cli --port 8013
```

The final primary gate passed Ruff, typing, backend tests and 84.1% backend
coverage, architecture boundaries, repository hygiene, generated API/stream
contracts, configuration reference, and TUI checks (31 files, 474 tests).
The disposal-error regression retains bridge authority until a confirmed retry.
Observation-outage tests passed: MLflow availability does not control commitment.

Baseline CLI execution used the frozen source checkout:

```sh
PYTHONPATH=/Users/zocho/.codex/worktrees/simplification-baseline/fleet-rlm/src \
  uv run --no-sync fleet cli --port 8014
```

Final execution used the actual `uv run fleet cli` supervisor and terminal client,
not a substitute runner. `/resume`, `/attach`, Escape cancellation, and `/exit`
were operated through the client. Product artifact content was retrieved with
`GET /api/artifacts/<artifact-id>/content`.

## Live expectations and evidence

Baseline Session: `328776f5-929c-4b15-abd3-e90b40a63ef6`.
Final Session: `a111a943-b326-41f9-9e57-53a0887b798e`.
MLflow experiment: `1`. Trace IDs below identify execution traces; companion
preparation traces carry the same Run ID and `fleet.trace_phase=preparation`.
Complete trace JSON and SQL snapshots are retained locally under the ignored
`.fleet_rlm/simplification-validation/` directory; credentials are excluded.

| Final scenario | Run / committed revision | Execution trace | Observed result |
| --- | --- | --- | --- |
| Root and isolated child | `cd4ed1ef-8898-4de2-b31f-a88f64baceca` / 1 | `tr-f58666f8e40ab3bafd6d57f0806096b9` | Root and child independently returned 333383335000; three reasoning/code steps each; child cleanup confirmed |
| Cold attachment analysis | `7959d6af-abcd-4371-adc9-f39db2a4031d` / 2 | `tr-17b8c8f8ce1587672dfe8798879e1305` | Real remote Python inspected all 10,000 CSV rows; exact sum and group counts matched independent verification; artifact committed |
| Memory and checkpoint | `db63bd38-9b8d-4e98-97e6-69b34dad5b34` / 3 | `tr-ff9219e26ea3f5d7c6a6b08b262f7a2a` | Canonical memory record `bf025e36` and active-task revision 2 persisted |
| Warm continuation | `a5268935-b314-47ac-bf21-b5936980757d` / 4 | `tr-76d3c6ae6aff40fc734e863b780a8571` | Same healthy Sandbox and binding generation reused; prior invocation variables absent; saved data read without recomputation |
| Restart | `02f22592-1199-46fa-9a9a-36d87a71d29f` / 5 | `tr-eb5ab2237282e848ba0d374b14abd240` | Previous-process marker absent; exact memory ID/learning and checkpoint goal/revision read successfully |
| Cancellation during remote sleep | `832777c6-67bd-4cdf-906d-50d4b8c72581` / none | `tr-57dd365965329a4113015a5c7f25600a` | Escape interrupted remote code; SQL status `cancelled`, no commit revision; provider independently confirmed stopped |
| Recovery | `aedd7842-6213-43bc-a9a9-be20f9ed5abd` / 7 | `tr-c3d00bec354a3192f36889eab0cdbdb7` | Fresh root read exact saved memory/checkpoint; cancelled variables absent; commit successful |
| Final-source recursive regression | `57adbada-c2d6-445a-a480-25e1217e1cdd` / 8 | `tr-994fc289bdb35109d1b28d2e4e9a8815` | Root and isolated child agreed on 333383335000; six model calls including three child calls/code steps; memory `a81880b1` saved; cleanup confirmed |


Companion preparation traces were also retrieved and inspected:

| Run | Preparation trace |
| --- | --- |
| `cd4ed1ef-8898-4de2-b31f-a88f64baceca` | `tr-39543169fd9a65d62203dc64969a8ae3` |
| `7959d6af-abcd-4371-adc9-f39db2a4031d` | `tr-a1cc8eb8ab7df85fc8a9f1da9330cddd` |
| `db63bd38-9b8d-4e98-97e6-69b34dad5b34` | `tr-63a1892430f8b98c139b5d499ed1732a` |
| `a5268935-b314-47ac-bf21-b5936980757d` | `tr-ffde60a21d869ec6439676b414b003f7` |
| `02f22592-1199-46fa-9a9a-36d87a71d29f` | `tr-7525bd3bd3d3f99a03ff1c6147995c7f` |
| `832777c6-67bd-4cdf-906d-50d4b8c72581` | `tr-b6e735a6eb95690bd69dd6824fca6850` |
| `aedd7842-6213-43bc-a9a9-be20f9ed5abd` | `tr-f51bf43814241387c6fbd5f3b2c65a81` |
| `57adbada-c2d6-445a-a480-25e1217e1cdd` | `tr-faa5741b0df4541f497dac261dfe874e` |

The CSV contains rows `i=1..10000`, value `i*i`, and group `i%7`.
Its SHA-256 is
`a014a183e0cba0e22c3d21479a506404b6dc63b314f400d29ea5ff31bf69d3d8`.
Expected sum is 333383335000; group counts are 1428 for groups 0, 5, and 6,
and 1429 for groups 1 through 4. Artifact
`375ea48b-20e9-4160-babc-03cc2a63cfc6` matched those values and remained
byte-for-byte identical after application restart. Session replay exposed the
committed history; SQL Run rows matched successful commit revisions.

Warm binding was `49b481b3-5c6d-4713-9225-8ca02d104be5`, generation 3, before
and after the Turn. Remote variables `mem_hits`, `task_res`, and `chk` were absent
before new assignments. Independent Daytona reads after the first shutdown
confirmed deletion of roots `2d4383f9-7ee9-4070-93aa-0c00849f90bb` and
`49b481b3-5c6d-4713-9225-8ca02d104be5`; child and final-Session root lists were
empty. Restart also preserved memory through an independent canonical-file read.

## Trace analysis and comparisons

For accepted successful traces, complete span trees were inspected for model
calls, remote code, tool outcomes, settlement, database commit, and cleanup.
There were no unfinished spans, orphaned parent IDs, or error spans in the five
final execution traces above. Recursive descendants included three child model
calls and three child Sandbox executions under the isolated-child parent.
Repeated observer wrapper spans were distinguished from actual executions.
Preparation and execution traces were correlated through Run ID and explicit
preparation-trace metadata. Baseline MLflow source metadata exposed a local
absolute path; final tracing assigns the stable `fleet-rlm` source name instead. Known credential values and private local path
prefixes were scanned; final traces used the fixed `fleet-rlm` source name.
Cancellation had the expected error spans in the root, DSPy execution, and
interrupted Sandbox execution, with no unfinished spans or missing parents.
The Sandbox code contained the marker, 45-second sleep, and subsequent sum;
Escape was delivered five seconds after dispatch, while the client showed the
remote step active. That code span was interrupted before submission. Its
budget-exhausted wrapper is a consequence of fencing the cancelled execution;
the Turn's terminal classification was `cancelled`.

The matched baseline recursive Run was
`ff11cdf8-efb1-4503-9cc8-f0c475f76f59`, trace
`tr-6f2507b97df46b41c430d163a6142f95`, revision 1. Both versions made six model
calls, with three root and three child steps. Baseline used 40,691 tokens and
133.188 seconds; final used 32,752 tokens and 79.998 seconds. Baseline repaired an
invalid child input shape; final did not need that repair.

Matched baseline attachment Run `cd538d90-a076-41c3-8384-9c19c1d67d5b`, trace
`tr-6b11f17bc5834fa99fbfe510493cba59`, revision 4, produced the same exact CSV
results and a readable artifact. Baseline took nine model iterations and
383.284 seconds; final took three iterations and 42.372 seconds. Baseline attempted
invalid artifact titles and extra probe artifacts before recovery. These are
single stochastic model runs, not evidence of a causal performance improvement.

The complete baseline warm Run was `dfe49ceb-3b83-4847-bd5e-d6514cc2d94f`,
trace `tr-de83cf1b86b11dc703cf3d7aa7222d27`, revision 11. Sandbox
`1f34986c-68f9-44a7-a08a-3e7250e46456`, generation 9, was unchanged across
the Turn; `S_direct` was absent before assignment. A file-listing request using
absolute `/` was rejected; the model recovered with the relative Workspace path.
Its three model iterations and seven host calls explain the extra work relative
to the final warm check. All spans finished and parent relationships were intact.


| Scenario | Actual model calls | Sandbox executions | Input / output tokens | Trace seconds |
| --- | --- | --- | --- | --- |
| Recursive baseline | 6 | 7 | 26916 / 13775 | 133.188 |
| Recursive final | 6 | 7 | 26097 / 6655 | 79.998 |
| CSV baseline | 9 | 11 | 79639 / 41011 | 383.284 |
| CSV final | 3 | 5 | 21833 / 4366 | 42.372 |
| Warm baseline | 3 | 4 | 22291 / 4629 | 58.626 |
| Warm final | 2 | 3 | 13719 / 16424 | 140.020 |
| Final-source recursive regression | 6 | 7 | 27708 / 17936 | 199.768 |

Sandbox counts include the actual history/context binding code, not only model
actions. The final warm Turn used fewer calls but generated 16,424 output tokens
versus 4,629 in the baseline, explaining its longer model-generation phase.

## Investigated validation failures

A baseline warm trace was interrupted when its MLflow server stopped. It is not
accepted as complete trace evidence. An early baseline attachment attempt was
contaminated by editing inspected Python source while the old application was
loaded; the frozen baseline checkout removed that harness error.

Final Run `4c495d25-1a08-4845-b1ae-4b70968c1e44` failed preparation while waiting
for workspace-I/O acquisition. Trace `tr-19ce5944e5929a40f1bfa3ab69535e88` has
two completed error spans, no model calls, no commit, and no Turn rows. Subsequent
prepared executions succeeded.

Restart attempt `cfaa00af-3457-45c7-ad48-2b4388dc2106`, trace
`tr-14afc1fce7c94d137f3af014aafe1886`, repeatedly called a checkpoint tool and
hit the existing no-progress guard. During this attempt the validation harness
also started another application against the same database; startup recovery
claimed its active Run, which settled as `stale_claim` without a commit.
Applications are now validated sequentially. Retry `02f22592-...` read the exact
durable memory and checkpoint once each and committed successfully.

The first cancellation probe finished its sleep before Escape was delivered and
committed as Run `14c887f1-5a45-480d-a895-dbf168d1bd9f`, revision 6. It is not
counted as cancellation evidence. The monitored retry above cancelled at base
revision 6; recovery committed revision 7, so cancellation did not advance the
successful checkpoint. Terminal failure projection rows are distinct from a
successful commit revision.

After recovery, the product artifact remained byte-for-byte identical. `/exit`
drained the application and the backend process ended without unresolved-cleanup
warnings. Independent SDK `get` calls confirmed deletion of the cancelled root
`c0f172ec-db56-4b52-9dfb-7e4ede0d4b05`, recovery root
`8bc77404-266d-4fd5-8eae-45910b8cbcbf`, restarted root
`22d64897-829b-46b5-8ddf-edfc69617509`, and baseline root
`3aad0f7f-742d-457b-a326-b9d816538f46`. Provider lists contained no roots for
either validation Session and no recursive children. The sequential baseline
root `1f34986c-68f9-44a7-a08a-3e7250e46456` was also independently confirmed
deleted, and the Workspace host-I/O provider list was empty.

## Final replay, shutdown, and completion

The final-source recursive trace contains 78 completed spans, no error spans,
and no missing parents. Its child invocation attributes are `call_index=1` and
`recursive_depth=1`; descendants contain three actual model calls and three
Sandbox executions. Total model calls remain six. This later regression ran
against accumulated Session history and used 45,644 tokens (27,708 input and
17,936 output) in 199.768 seconds. It generated substantially more root reasoning
than the initial empty-history run; model and remote execution counts were
unchanged. This later check is not a matched latency benchmark. No duplicate execution or unexplained failure
was found.

The product replay probe reused the final Run's original idempotency key and
exact persisted input. Its SSE `turn_start` reported
`runId=57adbada-c2d6-445a-a480-25e1217e1cdd` and `delivery=replay`; `turn_finish`
reported `status=completed`, `checkpointVersion=8`. SQL remained at 11 Runs and
18 Turn projection rows, and Sandbox `50beddcd-8202-424c-96d4-9d6652371c5b`,
generation 8, was unchanged. Replay returned the committed answer and trajectory
without acquiring an interpreter or executing another child.

Evidence retrieval and product readback used these exact commands:

```sh
uv run python .fleet_rlm/simplification-validation/inspect_live.py
uv run python .fleet_rlm/simplification-validation/inspect_final.py
uv run python .fleet_rlm/simplification-validation/verify_replay.py
curl -fsS http://127.0.0.1:8013/api/artifacts/375ea48b-20e9-4160-babc-03cc2a63cfc6/content \
  -o .fleet_rlm/simplification-validation/final-artifact-after-cancellation.json
cmp .fleet_rlm/simplification-validation/final-artifact.json \
  .fleet_rlm/simplification-validation/final-artifact-after-cancellation.json
```

The inspection helpers use `MlflowClient.search_traces(locations=["1"])` and
`get_trace(trace_id, display=False)` against the local MLflow store, exporting
complete trace JSON. SQL inspection uses the configured database with
`RunRow`, `TurnRow`, and `SandboxBindingRow`, comparing status, commit revision,
and projected Run ownership. These credential-free helper files and exports
remain in the ignored local evidence directory.

After the last `/exit`, the supervisor and backend ended normally. Independent
Daytona SDK `get("50beddcd-8202-424c-96d4-9d6652371c5b")` returned NotFound,
confirming deletion. Exhausted `list(ListSandboxesQuery(labels=...))` results
were empty for each validation `session_id`, `fleet.runtime=recursive-child`,
and `fleet-package=fleet_rlm` plus the validation `workspace_id`. SQL query
`RunRow.status.in_(["running", "settling"])` returned no rows. These checks are
recorded in `final-cleanup-receipt.json`; trace success was not used as proof of
provider deletion. All 21 final preparation/execution traces passed the complete
credential-value and private-path scan. Expected cancellation errors and the
excluded attempts described above were inspected separately.

The baseline checkout was archived with a recoverable Git snapshot after its
application stopped. The primary working tree and unrelated edits remain
uncommitted. `make check`, `make check-docs`, and `git diff --check` passed on the
completed implementation. All required live scenarios have successful accepted
runs, complete trace analysis, durable-state verification, and confirmed cleanup.

After completing shutdown validation, a fresh application instance was started
with the exact command `uv run fleet cli` in tmux session `fleet-simplify-final`
and left running at the operator's request. FastAPI's
`http://127.0.0.1:8000/openapi.json` and MLflow's
`http://127.0.0.1:5001/health` both returned HTTP 200. This fresh instance is
separate from the completed validation shutdown recorded above.
