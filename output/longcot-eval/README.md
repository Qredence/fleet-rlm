# LongCoT Benchmark Output Directory

This directory contains organized results from the LongCoT benchmark evaluation comparing direct (baseline) LLM inference against the fleet-rlm recursive language model workspace.

## Directory Structure

```
longcot-eval/
├── final/              # Final merged results (100 tasks each)
│   ├── direct_100_tasks.jsonl        # Direct baseline results (100 tasks)
│   ├── rlm_100_tasks.jsonl           # RLM results (100 tasks)
│   ├── direct_eval.json              # Direct baseline evaluation scores
│   ├── rlm_eval.json                 # RLM evaluation scores
│   ├── comparison_report.md          # Comprehensive comparison report
│   └── comparison_summary.md         # Summary comparison report
├── pilot/              # 20-task pilot results
│   ├── direct_20_tasks.jsonl         # Direct baseline pilot (20 tasks)
│   └── rlm_20_tasks.jsonl            # RLM pilot (20 tasks)
├── partial-runs/       # Individual partial runs (not merged)
│   ├── chess-math/                   # Partial chess/math category run
│   ├── cs-chem/                      # Partial CS/chemistry category run
│   └── *.jsonl                       # Various partial run files
├── logs/               # Log files from benchmark runs
│   ├── pilot-*.log                   # Pilot run logs
│   └── full-direct-*.log             # Full direct benchmark run logs
└── archive/            # Old reports, transport summaries, original files
    ├── comparison-report-*.md        # Earlier comparison reports
    ├── longcot-eval-*.json           # Individual eval JSONs (non-merged)
    └── longcot-*.json / *.jsonl      # Original copies of final files
```

## File Details

### `final/`
Contains the definitive benchmark results. Files here are copies with canonical names for easy reference.

- **`direct_100_tasks.jsonl`** — Direct baseline (OpenRouter DeepSeek V4 Flash) on 100 LongCoT-mini tasks
- **`rlm_100_tasks.jsonl`** — RLM workspace (recursive ReAct agent) on 100 LongCoT-mini tasks
- **`direct_eval.json`** — Aggregated evaluation scores for direct baseline
- **`rlm_eval.json`** — Aggregated evaluation scores for RLM
- **`comparison_report.md`** — Full comprehensive comparison report with per-category breakdowns
- **`comparison_summary.md`** — Condensed summary comparison report

### `pilot/`
20-task pilot runs conducted on 2026-04-30 before the full 100-task benchmark.

### `partial-runs/`
Individual batch runs that were later merged into the final `*_MERGED_100.jsonl` files. Includes earlier Nemotron probe runs from 2026-04-29.

### `logs/`
Console output and runtime logs from benchmark execution sessions.

### `archive/`
Original copies of files that were renamed/copied into `final/`, plus earlier comparison reports and individual (non-merged) evaluation JSONs. Kept for provenance and audit trail.

## Related Directories

Other benchmark-related directories in `output/`:

| Directory | Status | Description |
|-----------|--------|-------------|
| `longcot-eval-rlm/` | Kept | Early RLM eval with easy subset |
| `longcot-eval-rlm-nemotron/` | Kept | Empty — placeholder for nemotron evals |
| `longcot-eval-rlm-nemotron-hard/` | Kept | Nemotron hard subset evals with merged results |
| `longcot-eval-rlm-nemotron-l4/` | Kept | Nemotron L4 tier evals |
| `longcot-eval-rlm-nemotron-mini/` | Kept | Nemotron mini subset evals |
| `rlm-eval/` | Kept | S-NIAH benchmark results |
| `rlm-eval-full/` | Kept | Full unified evaluation (S-NIAH, OOLONG, workspace) |
| `rlm-eval-oolong/` | Kept | OOLONG-specific results |
| `rlm-eval-smoke/` | Kept | Smoke test results |
| `benchmark-archive/` | Archive | Probe/test directories from development |

## Date Range

- **Pilot runs**: 2026-04-29 to 2026-04-30
- **Full 100-task benchmark**: 2026-04-30 to 2026-05-01
