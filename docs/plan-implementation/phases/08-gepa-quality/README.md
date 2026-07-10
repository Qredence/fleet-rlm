# GEPA quality dossier

## Phase 8 — GEPA and quality lane

- **Order:** `8`
- **Status:** `planned`
- **Track:** `Config`
- **Summary:** Run evaluation and DSPy program optimization offline from normal chat execution.

### Goal and target ownership

`src/fleet_rlm/quality/` owns datasets, metrics, GEPA execution, results, and
explicit promotion. Optimization may log to MLflow when configured, using a
separate experiment from runtime traces by default.

```text
fleet-runtime-traces
fleet-gepa-optimization
fleet-evals
```

GEPA configuration preserves its budget contract (`auto`, `max_metric_calls`,
or `max_full_evals`) and records candidates/results separately from user chat.

### Non-goals

- Run GEPA inside normal `/api/chat` turns.
- Optimize prompts from production data without an explicit policy.
- Make GEPA or MLflow required for direct RLM or default tests.
- Promote an optimized program without reviewable evidence.

### Acceptance criteria

- [ ] Selected DSPy modules can be optimized offline against explicit datasets.
- [ ] Optimization progress can be logged to MLflow when enabled.
- [ ] Results are separate from runtime chat traces.
- [ ] Promotion is explicit, reviewable, and reversible.
- [ ] Normal chat execution is unaffected when quality features are disabled.

### Validation

```bash
uv run pytest tests/unit/quality/
```
