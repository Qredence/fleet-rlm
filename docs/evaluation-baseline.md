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

## Multipart, tracing, and skill fidelity requalification, 2026-11-26

Compared merged main `e665bff6a` with the runtime and skills at `4bf6b19ff`. Each arm had a separate Neon database and object-storage endpoint, skill installation directory, and MLflow experiment. Both used the same Daytona image, serving and decision models, disabled DSPy caches, fixture set, and repeat counts as the preceding qualification.

| Check | Fresh main | Corrected candidate |
| --- | ---: | ---: |
| Exact primary, secondary and abstain routing across five 41-case holdouts | 205/205 | 205/205 |
| CSV replacement and recursive reconciliation, including trace verification | 4/10 | 10/10 |
| Recovery trace IDs retrieved directly from SSE links | 8/15 | 15/15 |
| Maximum sandbox acquisition in retrievable recovery traces | 2.87 s (8 observations) | 21.56 s (15 observations) |
| Median Python execution after each worker's first cell | 0.473 s (110 cells) | 0.475 s (201 cells) |

The 15 candidate recovery traces each had an independent `fleet.turn` root, the expected run tag, and Daytona execution spans. A separate client disconnect produced a retrievable cancelled root and the test session was deleted (HTTP 204). One previously missing ID on main had spans in the tracker database beneath the incoming FastAPI HTTP request while direct lookup reported incomplete export. Giving the worker its own root resolved the observed recovery failure. Occasional MLflow span-start warnings for `Predict.forward` remained. [Cancelled trace](http://127.0.0.1:5001/#/experiments/53/traces?traceId=tr-a388a8523cd5171d42e5296f1e5c904d)

On staged Neon storage, a sibling upload remained open while Fleet deleted the first session. Completing the sibling upload and reading its original bytes succeeded. Offline coverage additionally exercised a foreign-only page followed by owned keys and two upload IDs for the same key.

An independent generated-skill probe used an approved queue-label rule. The generated skill installed, and a fresh session returned the exact required handoff token from the label alone. All four opt-in live engine tests passed with the bundled skill registry. A preliminary test invocation pointed at an empty installation directory and failed on a missing `data-analysis` entry. The full local `make check` passed with 246 backend and 41 frontend tests. The four opt-in cases were skipped by the offline command. [Handoff experiment](http://127.0.0.1:5001/#/experiments/53/runs/b6900868ff51466298423ea962f7e3e5)

**Release decision — hold.** The corrected candidate passed **68/70** scheduled bundled checks. Fresh main passed **65/70**. Both candidate misses were paired regressions in the PDF workflow. Diagnostic probes are excluded from these totals. [Reference bundled run](http://127.0.0.1:5001/#/experiments/51/runs/ec8f6534b2894271b41af72e7d59a0e3) · [Candidate bundled run](http://127.0.0.1:5001/#/experiments/53/runs/227f672da03f4baea28d7805e45394d9)

| Bundled workflow | Main passes | Candidate passes |
| --- | ---: | ---: |
| PDF page evidence | 4/5 | 3/5 |
| Automatic workbook routing | 4/5 | 5/5 |
| Create skill from approved session rules | 5/5 | 5/5 |
| Reuse installed skill in a fresh session | 2/5 | 5/5 |
| Each of the other ten workflows | 5/5 | 5/5 |

All 69 scored candidate turns had correlated traces with Daytona evidence, compared with 48 of 69 scored reference turns. Each arm had one unscored turn. On main, workbook routing repeat 3 hit a sandbox startup error. Three reference reuse answers withheld approval despite the four supplied receipt IDs. The candidate returned the approved token in all five fresh sessions and on a separate check at the exact three-receipt boundary. The candidate server recorded no HTTP 503 or `NoSuchUpload` during this qualification.

- **PDF repeat 1** returned the correct count of 42 and correctly marked page 2 for OCR, but also attributed the count to page 2. The fixture places the sentence on page 1 and an image on page 2. The expected skill was active.
- **PDF repeat 4** disconnected without a final response. PostgreSQL rejected a NUL character during the JSONB update of `runs.ui_parts`. On the candidate database, the affected run belonged to the repeat-4 PDF session and still had status `running`. Its bound VM required manual cleanup. Daytona subsequently reported `destroyed`, and it was absent from the active inventory. Release follow-up must cover finalization with binary-derived tool output, terminal run state, automatic VM cleanup, and correct page attribution.

Private evidence is retained locally under `.fleet-evidence/requalification-4bf6b19ff/`. The reference, candidate, intermediate diagnostic, and offline-check Neon branches were disposed of after their runs.

## PDF attribution and binary transcript finalization, 2026-11-26

The earlier incorrect citation came from a PDF with shared image resources. The parser had correctly extracted “North depot shipped 42 crates” from physical page 1, while the image resource was discoverable from both pages. The extraction skill now verifies the figure against its own page and distinguishes resource references from content drawn on that page.

A controlled probe used the production HTTP, Neon and Daytona paths with a synthetic agent result. An embedded NUL in the answer, history and nested tool output completed with a visible `␀` in PostgreSQL. A second turn deliberately supplied an invalid JSON key after starting a child. PostgreSQL rejected the write. The fallback persisted a failed root, terminated the child, retired the VM and admitted a successful retry on a different VM. Deleting the test session returned HTTP 204. The database regression test also covers literal backslash-u text.

Fresh main `e665bff6a` passed 3/10 recovery workflows including traces and 43/70 scheduled bundled cases. Six of its 15 advertised recovery traces resolved directly. Its bundled results comprised 43 passes, three scored failures, 23 execution errors and one dependent reuse case blocked by creation. [Fresh main bundled run](http://127.0.0.1:5001/#/experiments/54/runs/3b576a328352482ba5989063bb22c5d0)

An intermediate candidate at `2083be75b` passed 10/10 recovery cases with all 15 traces retrievable. A reuse answer appended a global-memory token to the exact code requested by the user. The installed rule contained the correct code. The revised workspace instructions give the explicit output request precedence over recalled preferences.

The completed intermediate bundled runs scored 68/70 at `2083be75b` (one PDF timeout and one extra reuse token), 69/70 at `d342db443` (one wrong physical page), and 69/70 at `52a64392f` (one CSV source-total error). The wrong-page trace showed a correct passage on page 1 followed by a manually typed page 2 in the submitted JSON. The revised PDF skill carries the matched value, supporting passage and page number together into submission. On the CSV miss, €200 included only rows in the regional breakdown. The unfiltered file contained another €7 without a region and €13 in a void row. The revised data-analysis skill checks the €220 source total independently of grouped values. The prompts, fixtures and scorers used for the runs were unchanged.

**Final candidate `681600907` — release on hold.** Recovery and bundled workflows ran on separate fresh staging branches in parallel. All 10 recovery cases passed, and all 15 SSE-advertised IDs resolved to independent Fleet roots with matching run tags and Daytona execution evidence. Maximum acquisition time was 4.83 seconds across 15 observations. Median warm Python execution was 0.468 seconds across 181 cells (fresh main: 0.466 seconds across 139 cells). The offline `make check` passed 247 backend and 41 frontend tests. Three of four opt-in live engine tests passed on this candidate. The two-turn CSV test reached its history-growth check with three messages before and after the second turn; the assertion required an increase. The independent routing holdouts on main and on the earlier candidate both matched 205/205 expected decisions.

**Bundled workflows: 65/70 passed on the final candidate.** All five CSV repeats returned the correct regional amounts and €220 source total. All five PDF answers reported 42 crates and cited physical page 1. Repeats 3 and 5 returned an empty OCR list instead of flagging page 2, leaving the complete PDF gate at 3/5. All 67 scored turns had correlated traces. Two further cases were unscored after HTTP 503 on session deletion (installed-skill reuse repeat 5 and automatic no-skill repeat 2); both deletions subsequently returned HTTP 204. Automatic greeting repeat 2 was unscored after a workspace startup failure. Cleanup retries do not change the qualification totals. [Final bundled run](http://127.0.0.1:5001/#/experiments/62/runs/2f99b05c1c7a4daebd892a9ec077b01b)

The qualification gate remains in force. PR #760 is held on its branch. Private per-repeat reports and cleanup evidence are retained under `.fleet-evidence/pdf-csv-qualification-681600907/`.

## Research skills and artifact qualification, 2026-11-26

**Release decision: hold.** The proposed backend wheel contains ten discoverable skills. PDF assembly and cited document extraction have separate instructions. Excel creation, editing and audit share `xlsx`. Existing `@spreadsheet-audit` pins resolve to that skill. Markdown briefs use `report-writing`. Word and PowerPoint are independent evaluation candidates. Their basic cases tied the no-skill arm on correctness, and their harder template cases remain unscored.

The reference was checkout `341a8f796b251e54f6aba3699b6e47fe43160486`. Reference, no-skill and alternative instructions ran against a sandbox with the same analysis, OCR, recalculation and rendering tools. The original sandbox lacked Tesseract, Poppler, LibreOffice and the Word and PowerPoint Python libraries. The shared image supports the Office workflows even without special instructions. Runs used the Jev decision model (`typesafe:jev-1.14.0`) and DeepSeek Flash (`openai/deepseek/deepseek-v4.1-flash`) with DSPy caching disabled. Each scheduled comparison used five repeats. The results below identify the specific revisions or arms that returned evidence. They do not substitute intermediate runs for qualification of the final package.

### First-turn selection

The control and the final ten-skill registry both matched all **205/205** decisions on the original 41 holdout prompts. Across the 14 additional in-scope prompts, the control matched **35/70** and the ten-skill registry matched **70/70**. This includes the same XLSX filename with requests for record analysis, formula audit and workbook editing. Across the entire expanded set, the totals were **240/305** and **275/305** respectively. The remaining 30 decisions request one of the two deferred Office skills. Their expected labels were retained. The twelve-skill exploratory registry matched **295/295** on the earlier set, before the two harder Office prompts were added. These are repeated measurements on fixed phrasings.

### Independently checked results

| Workflow | Output evidence and decision |
| --- | --- |
| Extract two exhibits and assemble a cited appendix | Reference **1/5**, no skill **2/5**, revised PDF assembly **5/5**. Downloaded pages matched the scanned and native originals in the requested order. Median elapsed time and Python actions fell from **173 s / 20** on the reference to **27 s / 5**. Simple reorder and rotation passed **4/4 scored** on the final instructions. One repeat hit an infrastructure failure, including on retry. |
| Attribute text and classify pages for OCR | On the corrected fixture, the reference scored **0/5**, a refined combined PDF skill **3/5**, and the separate extraction skill **5/5**. A native page with 42 crates, a scanned VA74 note and an illustration-only page exercise distinct evidence requirements. Plain-text section extraction remains a separate case. |
| Author, edit and audit XLSX | The combined workbook skill passed **5/5 each** on authored workbooks, repairs and audits. The split authoring skill repaired **4/5**. The no-skill arm authored **4/5** and passed **5/5** repairs and audits. The reference passed all three on the upgraded image. Recalculation of a downloaded copy after changing 8 to 18 had to move the total from 20 to 30. The audit included a plausible SUM range that omitted a detail row. |
| Sourced Markdown brief | Reference, candidate and no skill passed all five file and citation checks. Four of five candidate files opened immediately after the title with both counts and the unresolved gap. All five no-skill files opened with audience or file metadata. Median candidate time was **31 s** versus **22 s** with no skill. This is a reader-facing improvement with a latency cost. |
| Reconcile records and preserve uncertainty | Five of five candidate answers reconciled gross and net units, named both source files and left an unsupported damage count unresolved. Reference and no skill also passed five of five. The concise evidence-centered instructions improved first-turn selection. |
| Word | The candidate and no-skill arms each passed **5/5 creation** and **5/5 basic edits**. The candidate took median **36 s / 7 actions** to create a report, compared with **17 s / 3** with no skill. All ten candidate pages were rendered. The bold-number and italic-unit template case had no scored executions. Defer automatic inclusion. |
| PowerPoint | Both arms passed **5/5 creation** and **5/5 basic edits**. Median deck creation took **110 s / 11 actions** with the candidate and **28 s / 3** with no skill. All twenty candidate slides were rendered. The editable-chart and unchanged-appendix comparison had no scored executions. Defer automatic inclusion. |
| Web research and generated skills | The direct URL, discovery and follow-up suite passed **15/15** with revised web instructions, **13/15** on the reference and **0/15** without the skill. An intermediate candidate passed **5/5 each** on approved-memory skill creation, pinned reuse and fresh automatic reuse. The no-skill arm passed **0/5** creator checks. Final-package reuse remains open. |

The reference passed **69/75** incumbent workflows. The later candidate returned **52 passes, zero scored failures and 13 unscored attempts**. Ten dependent reuse checks could not run after the five creator attempts failed to execute. Provider billing errors also prevented live checks of the stricter SVG bar geometry, the harder Office fixtures and the final wheel's artifact-download smoke test. Five valid outputs per canonical workflow and a complete incumbent comparison are still required. The selected ten-skill wheel was independently unpacked and its ten bodies and legacy alias verified. Its staged amd64 image is `img_GraWD9vrtr73bds11dJ5JF` in `us-east-ash-1`. The local shared check passed 247 backend tests and 41 frontend tests. Four opt-in backend tests were skipped.

The harness records selection and output separately, retains downloaded artifacts, and records per-turn duration, Python actions and trace IDs. The PDF scorer compares rendered output with the requested original pages. The replay accepts JSON surrounded by artifact handoff text and checks Word citations in paragraphs and tables. The same replay was applied to the saved comparison artifacts. Runs and raw outcomes are retained under `.fleet-evidence/catalog-341a8f796/`. [Reference incumbent run](http://127.0.0.1:5001/#/experiments/63/runs/86725bee536749158ad91bed7f54885b) · [Candidate incumbent run](http://127.0.0.1:5001/#/experiments/68/runs/84a0de5a787042edac7e46eb57e59662) · [PDF assembly run](http://127.0.0.1:5001/#/experiments/69/runs/ef2c7e67b99e47fdb69197f1076722d1) · [Workbook comparison](http://127.0.0.1:5001/#/experiments/66/runs/1afa753075a84b589ce76139fc5259f3) · [Final wheel smoke](http://127.0.0.1:5001/#/experiments/72/runs/9558417f591140f8ac5eeb65ee814044)

To reproduce, start a staging Fleet backend with the intended registry, an isolated database and the matched image. From `backend/`, use the following commands with the host's staging environment. Artifact verification also uses `soffice` and `pdftoppm` on the evaluation host. Set `FLEET_SKILLS_DIR` to the matching registry for standalone selector runs. The Office and alternative-format instructions live under [skill candidates](../backend/eval_datasets/skill_candidates/).

```bash
eval_uv=(uv run --locked --with matplotlib --with pillow --with openpyxl --with pypdf --with python-docx --with python-pptx)
"${eval_uv[@]}" python ../scripts/catalog_skill_cases.py "$OUT/selector" --routing
"${eval_uv[@]}" python ../scripts/fleet_workflow_baseline.py --url "$FLEET_URL" --catalog-skills --repeats 5 --output-dir "$OUT/catalog"
"${eval_uv[@]}" python ../scripts/catalog_skill_cases.py "$OUT/catalog" --combined-workbook
"${eval_uv[@]}" python ../scripts/fleet_workflow_baseline.py --url "$FLEET_URL" --bundled-skills --repeats 5 --output-dir "$OUT/incumbent"
```

For a no-skill comparison, use an isolated reference copy with both its configured and bundled skill directories empty. Fleet falls back to bundled skills when the configured directory is empty. Confirm the recorded active list is empty and pass `--unpinned` to the bundled suite. For the combined PDF alternative, pass `--merged-formats` to both the live catalog runner and the replay. Preserve the raw result before using `--resume` to retry unscored executions.

### Frontend provider recovery and artifact delivery

On 2026-11-26, the workspace default sent the primary model request for `openai/deepseek/deepseek-v4.1-flash` to the Command Code endpoint. Fleet showed a billing failure naming the configured gateway and model. Selecting **OpenAI direct · gpt-6-luna** and clicking **Retry last request with selected model** produced a completed answer. The service behind the gateway that originated the billing failure remains unknown. The test used a session override on an isolated Neon branch. [Billing recovery chat](http://localhost:5173/#/sessions/01a21908-26ea-7176-911f-b19fd29ac0e1)

| Browser workflow | Verified result |
| --- | --- |
| Settings → Models | Created a provider with an inert synthetic key and reopened its editor. The key field was empty and displayed a masked saved-key hint. |
| [Report chat](http://localhost:5173/#/sessions/01a21900-8ea7-780b-b387-adfa22a62aed) | Opened handoff.md in Artifacts. The preview cited dispatch section 2 for 42 shipped and intake section 4 for 40 confirmed, and identified two unconfirmed crates. |
| [PDF chat](http://localhost:5173/#/sessions/01a21905-0a34-7195-b338-dd77fa4b7b90) | Uploaded depot.pdf through Workspace. A natural request activated pdf. Opened both pages in Artifacts and downloaded exhibits.pdf. Rendered output matched physical source pages 2 and 1 in order and orientation. |
| [Excel chat](http://localhost:5173/#/sessions/01a21905-921d-7e6e-a98a-bdf9d26c3960) | Uploaded ledger.xlsx through Workspace. A natural request activated xlsx. The first attempt ended without an artifact. A second request produced repaired.xlsx. Artifacts offered a download. The downloaded workbook retained its hidden row and Summary sheet, had a cached subtotal of 20, and recalculated to 30 after changing the second input from 8 to 18. |

These are single browser acceptance runs. The catalog's five-repeat release qualification remains open.
