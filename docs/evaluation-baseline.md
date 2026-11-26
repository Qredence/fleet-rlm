# Fleet DSPy evaluation baseline

Localhost MLflow links and local environment promotion below record the measured workstation runs. Deployment qualification requires its own evidence.

The baseline starts from checkpoint `75120dc1dc7f78af210fbd56e75156da82a8753f` on `checkpoint/fleet-runtime-state`. Run the scripts from `backend/` using the existing Fleet configuration and a local MLflow server. The scripts use reviewed synthetic cases from local traces. Keep row-level JSON, sandbox fixtures, and trace payloads in a private output directory outside Git.

```sh
cd backend
uv run --locked python ../scripts/dspy_baseline.py --pilot --repeats 1 --output-dir "$FLEET_EVAL_OUTPUT"
uv run --locked python ../scripts/dspy_baseline.py --output-dir "$FLEET_EVAL_OUTPUT"
uv run --locked python ../scripts/fleet_workflow_baseline.py --large --output-dir "$FLEET_EVAL_OUTPUT"
```

The workflow runner creates labeled sessions and synthetic memory candidates, then exercises session, chat, memory approval, skill pinning, and file endpoints. Use it on a Fleet environment where these test records and sandbox allocations are expected. The large checks reuse the two named QA sessions and their original files. Use `--resume /path/to/workflow.json` to preserve scored rows and retry missing executions. Browser checks additionally cover reload and artifact preview. The repeated HTTP measurements are reported separately from Browser evidence.

The DSPy runner disables model caching to measure independent inference. Seven fixture-free cases share the same reviewed inputs and serving model. It retains only relevant synthetic memories from the source traces. RLM runs with Fleet's Daytona interpreter and no additional tools. Tool-free Flex starts with its single-predictor implementation. The comparison excludes workspace-dependent cases, including the missing-file check, and makes no claim about Flex serving, delegation, or workspace execution.

## Live bundled Skill checks

Start the baseline and candidate against separate test Neon branches, Daytona volumes,
skills directories, and MLflow experiments. Use the same model IDs, snapshot, and budgets
for both arms, with model caching disabled. The environment file must point to the same
test database as the target HTTP server. The creator case seeds synthetic test memories
there and installs a test skill in the deployment's skills directory.

```sh
cd backend
uv run --locked --with openpyxl --with matplotlib --with pillow \
  --env-file "$FLEET_EVAL_ENV" python ../scripts/fleet_workflow_baseline.py \
  --bundled-skills --url "$FLEET_EVAL_URL" --repeats 5 \
  --output-dir "$FLEET_EVAL_OUTPUT"
```

Check `workflow.json` for the per-case outcomes, trace IDs, model calls, tokens, latency,
and Daytona execution evidence. Use `--case` to repeat a specific fixture or run
`--case skill-creator --case route-data-and-chart --case chart-svg` on the rebuilt
snapshot. Use `--case chart-png-fallback` with a sub-model endpoint that rejects image
inputs to verify the local PNG checks and artifact survive the failure. The runner deletes its test sessions and seeded global memories.
Discard the isolated skills directories when the evaluation is complete.

## Baseline results

| Surface | Correct | Median model calls | Median tokens | Median trace time |
| --- | ---: | ---: | ---: | ---: |
| Live Fleet HTTP workflows | 49/50 | | | |
| Offline RLM | 34/35 | 2 | 4,744 | 6.354 s |
| Offline Flex | 35/35 | 1 | 873 | 2.476 s |

- Live approval before and after, same-session recall, and fresh-session isolation each passed 5/5. Birch invalid input passed 4/5. The failure returned a checksum for a fraction even though the Birch skill was active. Daytona provisioning interrupted seven initial executions. After stopping the task's evaluation sandboxes, all seven scored retries completed. [Live workflow run](http://127.0.0.1:5001/#/experiments/1/runs/106f8121f36c4047ab2859586bf79388)
- The offline RLM miss returned the absent-memory fallback despite receiving the approved synthetic fact. Flex passed all cases on this fixture-free set. [RLM run](http://127.0.0.1:5001/#/experiments/1/runs/78502f9abb154cd6adbf45b83c8ca47b) · [Flex run](http://127.0.0.1:5001/#/experiments/1/runs/1c0e44e6cc6c47bd8f46fc3136b6a723)
- The original million-row CSV and 48 MB archive passed the live fixture, answer, and artifact checks. [CSV trace](http://127.0.0.1:5001/#/experiments/1/traces?workflowType=genai&traceId=tr-0bc607b43f195432ca5e5564a54473c9) · [Archive trace](http://127.0.0.1:5001/#/experiments/1/traces?workflowType=genai&traceId=tr-04b7422ed43ae71c55f9046450926b65)

## Optimization routes

Run the RLM correctness pilot after reviewing the baseline. Supply `FLEET_EVAL_DATABASE_URL` for an isolated Neon branch. The pilot uses the JSONL-equivalent reviewed fixture set from MLflow, so it does not read examples from Neon. Its database URL guard prevents selecting the serving endpoint.

```sh
uv run --locked python ../scripts/gepa_pilot.py --output-dir "$FLEET_EVAL_OUTPUT/gepa"
```

GEPA uses seven trace-derived training rows and six separately authored validation rows. The frozen holdout contains six additional cases from disjoint fixture lineages. The comparison runs the incumbent and candidate five times per holdout case. A candidate is eligible for a separate live canary only if it improves exact correctness, passes all blocking rows, and introduces zero paired regressions.

ReAnchor requires separately reviewed labels for automatic skill activation or memory save, update, skip, and scope. The baseline misses occurred during answer generation. There is no measured gate failure in this dataset to justify ReAnchor. Keep the current gate configurations and the serving RLM until there is a qualifying result.

## GEPA outcome

The incumbent and proposed action instructions tied at 6/6 on validation. GEPA selected the incumbent. Two independent passes of that selected program on the frozen holdout both scored 30/30. No artifact qualified for a live canary.

| Phase | Traced executions | Model calls | Tokens |
| --- | ---: | ---: | ---: |
| Search rollouts | 51 | 96 | 23,037 |
| Reflection | 1 | 1 | 8,259 |
| Incumbent holdout | 30 | 54 | 108,125 |
| Selected-program holdout | 30 | 58 | 120,740 |

The native budget was set to 50, and its final three-example batch brought the actual count to 51. The checked-in pilot now reserves room for the next batch and records the library's actual search count. That adjustment was checked offline. The live result above used the original native batching.

[GEPA pilot run](http://127.0.0.1:5001/#/experiments/1/runs/f1333d49e4224ea486074a103a8ec30a)

## Hardened offline optimization cycle (2026-11-25)

The hardened offline harness in `backend/src/fleet_rlm/optimize.py` runs ReAnchor and GEPA against lineage-disjoint JSONL datasets (`backend/eval_datasets/skills.jsonl`, `backend/eval_datasets/memory.jsonl`, `backend/eval_datasets/rlm.jsonl`, and `backend/eval_datasets/fixtures.py`) using an ephemeral Neon branch and canonical `FLEET_SKILLS_DIR=skills`.

```sh
cd backend
FLEET_SKILLS_DIR=skills FLEET_MLFLOW_EXPERIMENT=fleet-optimization-20261125 \
  uv run --locked --env-file .env env -u DAYTONA_API_URL -u DAYTONA_V2_API_URL \
  python -m fleet_rlm.optimize --target skills --database-url "$NEON_BRANCH_URL" \
  --dataset eval_datasets/skills.jsonl --output artifacts/skill_selector.json \
  --log-dir "$FLEET_OPT_LOG_DIR/skills"

FLEET_SKILLS_DIR=skills FLEET_MLFLOW_EXPERIMENT=fleet-optimization-20261125 \
  uv run --locked --env-file .env env -u DAYTONA_API_URL -u DAYTONA_V2_API_URL \
  python -m fleet_rlm.optimize --target memory --database-url "$NEON_BRANCH_URL" \
  --dataset eval_datasets/memory.jsonl --output artifacts/memory_gate.json \
  --log-dir "$FLEET_OPT_LOG_DIR/memory"

FLEET_DAYTONA_IMAGE=fleet-rlm-hardening-a5a3486ca757 \
  FLEET_SKILLS_DIR=skills FLEET_MLFLOW_EXPERIMENT=fleet-optimization-20261125 \
  uv run --locked --env-file .env env -u DAYTONA_API_URL -u DAYTONA_V2_API_URL \
  python -m fleet_rlm.optimize --target rlm --program rlm --database-url "$NEON_BRANCH_URL" \
  --dataset eval_datasets/rlm.jsonl --output artifacts/rlm_program.json \
  --max-metric-calls 80 --holdout-repeats 3 --num-threads 3 \
  --log-dir "$FLEET_OPT_LOG_DIR/rlm"
```

### Phase 3 offline optimization results

All runs logged to local MLflow [experiment 41](http://127.0.0.1:5001/#/experiments/41) (`fleet-optimization-20261125`).

| Target | Method | Split (train/val/holdout) | Baseline | Optimized | Gate outcome | MLflow run |
| --- | --- | --- | ---: | ---: | --- | --- |
| `skills` (`SkillSelector`) | ReAnchor | 73 / — / 41 | 0.8171 | 0.9512 | Discarded (`fp_rate 0.0 -> 0.25` on 2/8 negatives; `fn_rate 0.303 -> 0.0`) | [skills run](http://127.0.0.1:5001/#/experiments/41/runs/8b5561fefb3444dd8f0eeb6b4724fe90) |
| `memory` (`MemoryGate`) | ReAnchor | 65 / — / 35 | 0.7714 | 0.8286 | **Saved & qualified** (`fp_rate 0.25 -> 0.25`, `auto_approve_fp_count 0 -> 0`, `fn_rate 0.2105 -> 0.1053`, action/scope accuracy `0.7895 -> 0.8947`) | [memory run](http://127.0.0.1:5001/#/experiments/41/runs/f21e3b4160f14c809960eb9203cc5866) |
| `rlm` (root `dspy.RLM`) | GEPA (`66/80` search calls, 3 repeats) | 14 / 9 / 11 | 89.73 | 90.15 | Discarded (`instructions_changed=false`; base scored `99.44%` on val; `0` blocking failures, `0` paired regressions) | [rlm run](http://127.0.0.1:5001/#/experiments/41/runs/0fc8a5fe4b6e4fad8f16b25d27cc7e76) |

- `MemoryGate` calibration fitted `worth_saving.threshold = 0.06` and `action.weights = {"insert": 0.7, "update": 1.0, "skip": 1.0}` against resolved TypeSafe model snapshot `jev-1.14.0`, writing the untracked artifact `backend/artifacts/memory_gate.json`.
- Auditing the RLM holdout report surfaced a latent `_repeat` drop across `dspy.Example.with_inputs()`; fixed in commit `b9ae4bde6` by renaming to `eval_repeat` with a regression test in `backend/tests/test_phase5_optimize.py`.

### Phase 4 paired live HTTP canary and promotion

Both arms ran against isolated Neon branches, branch-scoped Neon Object Storage, isolated temporary `FLEET_SKILLS_DIR` directories (`skills_count=8` via `fallback_to_bundled=True`), `FLEET_DAYTONA_IMAGE=fleet-rlm-hardening-a5a3486ca757`, `openai/deepseek/deepseek-v4.1-flash`, and pinned `FLEET_DECISION_MODEL=typesafe:jev-1.14.0` (verified directly on the wire).

| Canary check | Baseline (`memory_gate_path=None`, [exp 42](http://127.0.0.1:5001/#/experiments/42)) | Candidate (`artifacts/memory_gate.json`, [exp 43](http://127.0.0.1:5001/#/experiments/43)) |
| --- | --- | --- |
| Live memory consolidation (8 turns/arm) | `TP=2/4` (`recall=0.50`), `FP=0/4`, `auto_approved_fp=0` | `TP=2/4` (`recall=0.50`), `FP=0/4`, `auto_approved_fp=0` |
| Duplicate memory turn (`N4`) | Skipped (`action=skip`, `p=0.88`) | Skipped (`action=skip`, `p=0.84`) |
| Stray post-turn memories after bundled smoke | `1` | `0` |
| Bundled skills smoke (`--repeats 2`, 6 cases / 14 rows) | `12/14` ([run](http://127.0.0.1:5001/#/experiments/42/runs/92ca109981eb404a959ff143dbff1aec)) | `11/14` ([run](http://127.0.0.1:5001/#/experiments/43/runs/f443b39da21b4c0b99be767235709b33)) |
| Live Tavily `web-research` + multi-turn follow-up | PASS (`web-research` active on T1, `[]` on T2) | PASS (`web-research` active on T1, `[]` on T2) |

- Both arms missed `route-workbook` (`0/2`, uncalibrated `SkillSelector`); the single `skill-creator-reuse` candidate repeat miss was traced to stochastic LLM authoring variance in the generated `SKILL.md` with zero `MemoryGate` involvement.
- **Promotion:** Enabled in untracked `backend/.env` with `FLEET_MEMORY_GATE_PATH=artifacts/memory_gate.json`, pinned `FLEET_DECISION_MODEL=typesafe:jev-1.14.0`, and `FLEET_SKILLS_DIR=skills`. `SkillSelector` and root `dspy.RLM` remain unoptimized until future candidates pass their promotion gates.

## Five-repeat routing release qualification, 2026-11-26

The candidate was merged main at `e665bff6a`. The reference was `d6bcbc84f`, immediately before the two Jev routing fixes. Both ran Daytona v2 using the same image and model settings on separate disposable Neon branches with branch-specific object storage, separate skills directories and separate MLflow experiments. DSPy memory and disk model caches were disabled. This comparison measures the routing release on v2. The historical v0 cutover is a separate qualification.

### Jev routing

| Metric | Reference | Candidate |
| --- | ---: | ---: |
| Primary skill present or correct abstention | 155/205 | 205/205 |
| Exact ordered skill list | 145/205 | 205/205 |
| Exact matches per 41-case repeat | 30, 30, 27, 29, 29 | 41, 41, 41, 41, 41 |

Each arm used its own bundled registry. The reference numbers exclude a preliminary run that loaded the candidate checkout's skill directory, which affected recognition of bundled host tools. The five passes reuse the same 41 labeled cases. They measure repeatability on that corpus.

### Live HTTP workflows

**Release decision — hold.** Candidate routing passed every automatic-selection case, but the candidate cleared only 56 of 70 scheduled bundled checks and 7 of 10 recovery checks with verified traces. Eight bundled case/repeat pairs that passed on the reference did not pass on the candidate. Diagnostic replays are excluded from all qualification totals.

Both deployments reported `openai/deepseek/deepseek-v4.1-flash` for the root and child model, `typesafe:jev-1.14.0` for decisions, image `img_MDWBfg4msegHiPCgBbJh7W`, 20 iterations, 50 LM calls, and maximum depth 4. The bundled runner starts five repeat lanes concurrently per arm. The recovery runner runs both cases concurrently within each repeat. [Reference bundled run](http://127.0.0.1:5001/#/experiments/49/runs/cb438e02703646e99e189472c8390c95) · [Candidate bundled run](http://127.0.0.1:5001/#/experiments/50/runs/da17f90b8bb446a598ae8e04cca5877a)

| Workflow (passes out of five) | Reference | Candidate |
| --- | ---: | ---: |
| Null-key data analysis | 4 | 5 |
| PDF page evidence | 2 | 2 |
| Workbook missing formula cache | 3 | 4 |
| SVG chart | 5 | 4 |
| PNG chart | 5 | 3 |
| Welch reference / invalid input | 5 / 5 | 4 / 4 |
| Recursive artifacts | 5 | 4 |
| Automatic data-and-chart / workbook | 5 / 3 | 5 / 5 |
| Automatic no-skill / greeting | 5 / 5 | 5 / 5 |
| Create skill / reuse installed skill | 2 / 2 | 4 / 2 |
| CSV recovery after forced VM replacement, including traces | 2 | 4 |
| Recursive reconciliation, including traces | 4 | 3 |

**Bundled denominators.** The reference had 56 passes, seven scored failures, four unscored errors, and three reuse checks blocked by failed creation. The candidate had 56 passes, three scored failures, ten unscored errors, and one blocked reuse. The runner attached traces with Daytona execution evidence to 45/63 scored reference turns and 43/59 scored candidate turns.

**Paired regressions and diagnosis**

- Seven reference passes became unscored on the candidate: PNG repeats 1 and 4, SVG repeat 2, PDF repeat 1, recursive artifacts repeat 5, Welch invalid repeat 1, and Welch reference repeat 4. Nine of the ten candidate execution errors were HTTP 503 on file upload. The remaining one failed during the turn; the candidate server logged `NoSuchUpload`. The reference also logged two `NoSuchUpload` failures. The storage endpoints and credentials were distinct between arms.
- A controlled probe on candidate storage created two multipart uploads under sibling prefixes. A list restricted to the first prefix returned **both** keys. Fleet's [session cleanup](../backend/src/fleet_rlm/storage.py) aborts every upload returned by that list without checking its key. This establishes a mechanism by which cleanup can abort a neighboring session's upload. The individual HTTP 503s do not expose enough provider detail to attribute each one conclusively. Before release, verify each key belongs to the deleting session, then exercise concurrent upload and deletion on staged Neon storage.
- The eighth paired regression was skill reuse repeat 1. The expected generated skill was active, but its instructions required merchant, date, and total for every receipt. The approved rule only required three receipts. Given four IDs, the agent returned `NOT APPROVED`. Candidate reuse repeat 5 similarly returned `NEEDS_REVIEW` after the generated skill added mandatory fields and blocking checks. Candidate creation repeat 3 produced an installable document that omitted the required approval token, blocking its reuse check. These failures occurred after skill selection. Verify generated rules preserve the approved condition before counting installation as successful. [Repeat 1 trace](http://127.0.0.1:5001/#/experiments/50/traces?traceId=tr-8e9e4400f61fd426987fcced0514abb7) · [Repeat 5 trace](http://127.0.0.1:5001/#/experiments/50/traces?traceId=tr-5cfd829747666cf6dd57f86ed4e0d7f8)
- Both arms passed the answer, artifact, trajectory, and HTTP session-cleanup assertions on all ten recovery cases before trace verification. The reference lost five of 15 turn traces across four cases. The candidate lost three across three cases: reconciliation repeats 3 and 5, and CSV repeat 4. All three candidate cases passed on the reference. Direct lookup of all eight SSE-advertised trace IDs returned `RESOURCE_DOES_NOT_EXIST` from MLflow. Increasing the search result limit alone cannot recover them. Both servers also logged span-start warnings; the exporter failure mechanism remains unresolved.
- In the available recovery traces, maximum sandbox acquisition was 3.57 seconds on the reference (10 observations) and 4.83 seconds on the candidate (12), below the 120-second bound. Median Python cell duration after the first execution on each worker was 0.815 seconds (146 reference cells) and 0.793 seconds (147 candidate cells). The observed medians clear the 25% slowdown threshold; missing traces limit coverage.

A single-lane diagnostic replay of PDF page evidence and automatic workbook routing passed both cases on both arms. The replay supports an intermittent failure and does not replace the original attempts. [Reference replay](http://127.0.0.1:5001/#/experiments/49/runs/dd16320cabdf43ea864e8bfaa5951804) · [Candidate replay](http://127.0.0.1:5001/#/experiments/50/runs/b0153e30e0124f1dbf626f6a192eee6c)

Both Neon branches were deleted. The final Daytona inventory contained no matches for the recorded test VM IDs or run-derived VM names. Private per-repeat JSON is retained locally under `.fleet-evidence/qualification-e665bff6a/{baseline,candidate}/`. Requalification requires a fresh five-repeat run after the multipart isolation, trace export, and generated-skill fidelity issues are addressed.
