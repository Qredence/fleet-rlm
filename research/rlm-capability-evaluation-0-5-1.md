# Research Notes: Fleet-RLM RLM Capability Evaluation (v0.5.1 branch)

## Scope

Write a blog post about the current branch changes with emphasis on:

- the new evaluation work
- the explanation and reporting improvements
- what these changes mean for fleet-rlm
- the next steps after the current branch

## Branch framing

- Current branch: `release/v0.5.1`
- The meaningful changes are in the working tree rather than a stack of branch-only commits.
- The theme is consistent across docs, release notes, and scripts: fleet-rlm is publishing an empirical RLM capability story.

## Files that define the story

- `docs/explanation/rlm-capability-evaluation.md`
- `output/rlm-eval-full/RESULTS.md`
- `scripts/evaluate_rlm_capabilities.py`
- `scripts/oolong_official_eval.py`
- `scripts/benchmarks/sniah.py`
- `scripts/benchmarks/oolong.py`
- `scripts/consolidate_rlm_results.py`
- `README.md`
- `CHANGELOG.md`
- `AGENTS.md`

## Core change

This branch changes fleet-rlm from a project that describes its recursive architecture to a project that measures it.

The major additions are:

- a unified harness for benchmark execution
- synthetic benchmark generators for S-NIAH and OOLONG-style tasks
- an adapter for Prime Intellect's official OOLONG environment
- a consolidated results report
- a new explanation document that states what is proven and what is still deferred

## Benchmark results to feature

### S-NIAH

- 50 tasks
- 100% accuracy
- covers large-context retrieval across depth and haystack size variation
- proves L1 and L2 are functioning end-to-end

### Synthetic OOLONG

- 30 tasks
- 0.7399 average score
- counting: 1.0000
- extraction: 1.0000
- classification: 0.2196
- useful framing: deterministic aggregation tasks are strong; fuzzy semantic classification remains harder

### Official OOLONG

- dataset: `oolongbench/oolong-synth`
- config: `trec_coarse`
- context length: 131,072 tokens
- run size completed: 12 tasks
- score: 0.9167
- paper comparison: 0.565 for RLM(GPT-5) in arXiv 2512.24601v2 Table 1
- delta often described in repo docs as +35.2 percentage points

## Why the official adapter matters

This is the strongest angle for the blog post.

Reasons:

- It uses an external benchmark rather than a project-owned toy dataset.
- It ports the official scoring rubric instead of inventing a compatible-looking metric.
- It creates a repeatable paper-comparable lane for future releases.
- It makes the evaluation story more credible because readers can inspect the adapter and reproduction commands.

## What is proven vs not proven

### Proven

- L1: sandbox code execution
- L2: large context stored as REPL variables rather than dumped into model context
- L3: recursive aggregation via `dspy.RLM`, `llm_query()`, and related paths

### Deferred / not yet proven

- L4: multi-pass recursive workspace orchestration via `RecursiveWorkspaceModule`
- statistical robustness at larger sample sizes
- generalization across more model families
- >128K paper-aligned contexts

## Bugs and blockers worth naming

Real evaluation exposed issues instead of only producing marketing numbers:

- DSPy v3 None-safety issues patched locally in the OOLONG adapter:
  - `_strip_code_fences()` tolerates `None`
  - `REPLHistory.append()` tolerates `None`
- fleet-rlm blockers for L4 evaluation:
  - `NoneType.strip()` failure in `delegate_to_rlm`
  - sandbox name collisions during batched child spawning

This is useful for the post because it shows the branch is honest about limits.

## Meaning for fleet-rlm

- The project now has a public benchmark-backed argument that it is a real RLM implementation.
- The recursive runtime story is easier to trust because it is backed by explicit artifacts and reproduction steps.
- The Optimization surface and Workbench can point to evidence instead of only capability claims.
- The repo now has a baseline for regression checking across future releases.

## Recommended narrative

1. Start with the problem: many systems claim "RLM" without paper-grade evidence.
2. Explain that this branch adds both measurement and explanation.
3. Make the official OOLONG adapter the centerpiece.
4. Be explicit that L4 is still deferred.
5. End on the next engineering milestone: fix L4 blockers and rerun the workspace benchmark.

## Useful commands

```bash
# from repo root
uv tool install -U prime
prime env pull primeintellect/oolong-rlm
uv run python scripts/oolong_official_eval.py \
  --subset synth \
  --split validation \
  --dataset-name trec_coarse \
  --context-len 131072 \
  --limit 20 \
  --output-dir output/rlm-eval-full/oolong-official
```

## Context7 notes

Official DSPy docs describe evaluation as running a module against a dev set with a metric, and describe optimizers like GEPA and MIPROv2 as offline compile-time optimization tools rather than live request-path behavior. That aligns with fleet-rlm's framing that benchmark and optimizer work should remain offline and inspectable.
