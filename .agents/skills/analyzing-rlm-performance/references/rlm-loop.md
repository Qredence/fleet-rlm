# RLM loop regressions

Inspect the affected prompt/program/tool seam in `src/fleet_rlm/rlm/` and the
resolved policy before choosing a tuning parameter. Separate native semantic
queries from opt-in Fleet child execution; verify which path the workload used.

Compare root iterations, model calls, input/output volume, retries, child calls,
terminal outcomes, and answer correctness on the same workload. Trace timing
alone does not establish useful child work or semantic success.

- If input volume grew, identify repeated instructions, pasted values, or
  redundant verification turns at their source. Preserve DSPy's history ownership.
- If iterations grew, inspect why the agent continued before reducing `max_iters`;
  a lower cap can suppress necessary verification or cause incomplete answers.
- If calls grew, inspect native call budgets and child admission separately.
  Preserve atomic admission and the fixed direct-child boundary.
- If retries dominate, inspect classification and deadline propagation. Compare
  transient recovery with added latency; deterministic policy/authentication/input
  failures are not evidence that a larger retry budget helps.
- Treat output limits as distinct from history management; measure whether lost
  evidence explains a quality regression before changing either projection limit.

Consult the pinned implementation and [DSPy RLM API](https://dspy.ai/api/modules/RLM/)
when changing native behavior. Add regressions at the affected program, budget,
or tool seam. Match quality as well as cost; do not recommend delegation merely
because the root performed work that a child could also perform.
