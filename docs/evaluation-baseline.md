# Fleet DSPy evaluation baseline

The baseline starts from checkpoint `75120dc1dc7f78af210fbd56e75156da82a8753f` on `checkpoint/fleet-runtime-state`. Run the scripts from `backend/` using the existing Fleet configuration and a local MLflow server. The scripts use reviewed synthetic cases from local traces. Keep row-level JSON, sandbox fixtures, and trace payloads in a private output directory outside Git.

```sh
cd backend
uv run --locked python ../scripts/dspy_baseline.py --pilot --repeats 1 --output-dir "$FLEET_EVAL_OUTPUT"
uv run --locked python ../scripts/dspy_baseline.py --output-dir "$FLEET_EVAL_OUTPUT"
uv run --locked python ../scripts/fleet_workflow_baseline.py --large --output-dir "$FLEET_EVAL_OUTPUT"
```

The workflow runner creates labeled sessions and synthetic memory candidates, then exercises session, chat, memory approval, skill pinning, and file endpoints. Use it on a Fleet environment where these test records and sandbox allocations are expected. The large checks reuse the two named QA sessions and their original files. Use `--resume /path/to/workflow.json` to preserve scored rows and retry missing executions. Browser checks additionally cover reload and artifact preview. The repeated HTTP measurements are reported separately from Browser evidence.

The DSPy runner disables model caching to measure independent inference. Seven fixture-free cases share the same reviewed inputs and serving model. It retains only relevant synthetic memories from the source traces. RLM runs with Fleet's Daytona interpreter and no additional tools. Tool-free Flex starts with its single-predictor implementation. The comparison excludes workspace-dependent cases, including the missing-file check, and makes no claim about Flex serving, delegation, or workspace execution.

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
