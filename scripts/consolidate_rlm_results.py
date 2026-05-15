#!/usr/bin/env python3
"""Consolidate the three RLM benchmark summaries into a single RESULTS.md report.

Reads the per-benchmark summary JSONs from output/rlm-eval-full/ and writes
a human-readable Markdown report with paper comparisons.

Usage:
    uv run python scripts/consolidate_rlm_results.py
    uv run python scripts/consolidate_rlm_results.py --input-dir output/rlm-eval-full
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "output/rlm-eval-full"

# Paper reference numbers (arXiv 2512.24601v2, Table 1, RLM(GPT-5) row)
PAPER_BASELINES = {
    "sniah": "not directly reported (RLM generally solves S-NIAH)",
    "oolong": 0.5650,
    "oolong_pairs": 0.5800,
    "codeqa": 0.6200,
    "browsecomp_plus": 0.9130,
}


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_first(*paths: Path) -> dict[str, Any] | None:
    """Return the first decodable JSON object from the provided candidate paths."""
    for path in paths:
        loaded = _load(path)
        if loaded is not None:
            return loaded
    return None


def _fmt_percent(val: float) -> str:
    return f"{val * 100:.1f}%"


def _fmt_score(val: float) -> str:
    return f"{val:.4f}"


def _fmt_baseline(label: str) -> str:
    baseline = PAPER_BASELINES[label]
    if isinstance(baseline, str):
        return baseline
    return _fmt_score(baseline)


def _fmt_sniah(summary: dict[str, Any]) -> list[str]:
    lines = [
        "## S-NIAH Benchmark (Needle in a Haystack)",
        "",
        f"- **Tasks**: {summary['tasks_total']}",
        f"- **Accuracy**: {_fmt_percent(summary['accuracy'])}",
    ]
    if summary.get("by_depth"):
        lines.append("")
        lines.append("### Accuracy by needle depth")
        lines.append("")
        lines.append("| Depth | Accuracy |")
        lines.append("|---|---|")
        for depth, acc in sorted(summary["by_depth"].items()):
            lines.append(f"| {depth} | {_fmt_percent(acc)} |")

    if summary.get("by_size"):
        lines.append("")
        lines.append("### Accuracy by haystack size")
        lines.append("")
        lines.append("| Size (chars) | Accuracy |")
        lines.append("|---|---|")
        for size, acc in sorted(summary["by_size"].items(), key=lambda x: int(x[0])):
            lines.append(f"| {int(size):,} | {_fmt_percent(acc)} |")

    if summary.get("by_type"):
        lines.append("")
        lines.append("### Accuracy by needle type")
        lines.append("")
        lines.append("| Type | Accuracy |")
        lines.append("|---|---|")
        for ntype, acc in sorted(summary["by_type"].items()):
            lines.append(f"| {ntype} | {_fmt_percent(acc)} |")

    return lines


def _fmt_oolong(summary: dict[str, Any]) -> list[str]:
    lines = [
        "## OOLONG Benchmark (Aggregation)",
        "",
        f"- **Tasks**: {summary['tasks_total']}",
        f"- **Avg score**: {_fmt_score(summary['avg_score'])} (paper metric: 0.75^|y−ŷ|)",
        f"- **Perfect (≥0.99)**: {summary.get('perfect_scores', 0)}",
        f"- **Near-miss (0.5–0.99)**: {summary.get('near_miss', 0)}",
        f"- **Failures (<0.5)**: {summary.get('failures', 0)}",
    ]

    if summary.get("by_type"):
        lines.append("")
        lines.append("### Score by task type")
        lines.append("")
        lines.append("| Type | Score |")
        lines.append("|---|---|")
        for ttype, score in sorted(summary["by_type"].items()):
            lines.append(f"| {ttype} | {_fmt_score(score)} |")

    return lines


def _fmt_oolong_official(summary: dict[str, Any]) -> list[str]:
    lines = [
        "## OOLONG Benchmark — Official Prime Intellect Environment",
        "",
        f"- **Tasks**: {summary['tasks_total']}",
        f"- **Avg score**: {_fmt_score(summary['avg_score'])} (paper RLM(GPT-5): {_fmt_baseline('oolong')})",
        f"- **Dataset**: {summary.get('dataset_name') or 'all'}",
        f"- **Context length**: {summary.get('context_len') or 'mixed'}",
        f"- **Perfect (≥0.99)**: {summary.get('perfect_scores', 0)}",
        f"- **Near-miss (0.5–0.99)**: {summary.get('near_miss', 0)}",
        f"- **Failures (<0.5)**: {summary.get('failures', 0)}",
    ]

    if summary.get("by_answer_type"):
        lines.append("")
        lines.append("### Score by answer type")
        lines.append("")
        lines.append("| Type | Score |")
        lines.append("|---|---|")
        for answer_type, score in sorted(summary["by_answer_type"].items()):
            lines.append(f"| {answer_type} | {_fmt_score(score)} |")

    return lines


def _fmt_workspace(summary: dict[str, Any]) -> list[str]:
    lines = [
        "## Workspace Benchmark (L4 Recursive Orchestrator)",
        "",
        f"- **Tasks**: {summary.get('total_tasks', 0)}",
        f"- **Single-pass avg coverage**: {_fmt_percent(summary.get('single_pass_avg_coverage', 0.0))}",
        f"- **Multi-pass avg coverage**: {_fmt_percent(summary.get('multi_pass_avg_coverage', 0.0))}",
        f"- **Multi-pass wins**: {summary.get('multi_pass_wins', 0)}/{summary.get('total_tasks', 0)}",
    ]

    hard = summary.get("hard_tasks", {})
    easy = summary.get("easy_tasks", {})
    if hard.get("count"):
        lines.append("")
        lines.append("### By task difficulty")
        lines.append("")
        lines.append("| Category | Count | Single-pass | Multi-pass |")
        lines.append("|---|---|---|---|")
        lines.append(
            f"| Hard (requires_multi_pass) | {hard['count']} | "
            f"{_fmt_percent(hard.get('single_avg', 0.0))} | "
            f"{_fmt_percent(hard.get('multi_avg', 0.0))} |"
        )
        lines.append(
            f"| Easy (control) | {easy['count']} | "
            f"{_fmt_percent(easy.get('single_avg', 0.0))} | "
            f"{_fmt_percent(easy.get('multi_avg', 0.0))} |"
        )

    total_s = summary.get("total_single_elapsed_ms", 0)
    total_m = summary.get("total_multi_elapsed_ms", 0)
    if total_s and total_m:
        lines.append("")
        lines.append("### Latency")
        lines.append("")
        lines.append(f"- Single-pass total: {total_s / 1000:.1f}s")
        lines.append(f"- Multi-pass total: {total_m / 1000:.1f}s")
        lines.append(f"- Multi-pass overhead: {total_m / total_s:.1f}×")

    return lines


def build_report(input_dir: Path) -> str:
    sniah = _load_first(
        input_dir / "sniah-summary.json",
        input_dir / "sniah/sniah-summary.json",
    )
    oolong = _load_first(
        input_dir / "oolong-summary.json",
        input_dir / "oolong/oolong-summary.json",
    )
    oolong_official = _load_first(
        input_dir / "oolong-official-summary.json",
        input_dir / "oolong-official/oolong-official-summary.json",
    )
    workspace = _load_first(
        input_dir / "workspace-summary.json",
        input_dir / "workspace/workspace-summary.json",
    )

    sections: list[str] = [
        "# Fleet-RLM Capability Evaluation — Full Results",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "Comparison baseline: arXiv 2512.24601v2 (RLM paper) Table 1, RLM(GPT-5).",
        "",
        "---",
        "",
    ]

    if sniah:
        sections.extend(_fmt_sniah(sniah))
        sections.append("")
        sections.append("---")
        sections.append("")

    if oolong:
        sections.extend(_fmt_oolong(oolong))
        sections.append("")
        sections.append("---")
        sections.append("")

    if oolong_official:
        sections.extend(_fmt_oolong_official(oolong_official))
        sections.append("")
        sections.append("---")
        sections.append("")

    if workspace:
        sections.extend(_fmt_workspace(workspace))
        sections.append("")
        sections.append("---")
        sections.append("")

    sections.extend(
        [
            "## Summary: What This Proves",
            "",
            (
                "- **L1 (Code execution)**: Every successful task demonstrates the RLM "
                + "writes Python, executes it in Daytona, and returns structured output via SUBMIT()."
            ),
            (
                "- **L2 (Large context as REPL variable)**: S-NIAH tasks process 50K–200K char "
                + "haystacks without the LLM seeing them in-context."
            ),
            (
                "- **L3 (Recursive sub-calls)**: Exercised via OOLONG classification tasks that "
                + "require iterating over hundreds of items."
            ),
            (
                "- **L4 (Recursive workspace orchestration)**: Multi-pass decomposition + "
                + "verification + repair loop, measurable via workspace benchmark."
            ),
            "",
            "## Paper Comparison",
            "",
            "| Benchmark | Paper RLM(GPT-5) | Fleet-RLM (this run) |",
            "|---|---|---|",
        ]
    )

    if sniah:
        sections.append(f"| S-NIAH | (solved) | {_fmt_percent(sniah['accuracy'])} |")
    if oolong_official:
        sections.append(f"| OOLONG-Official | {_fmt_baseline('oolong')} | {_fmt_score(oolong_official['avg_score'])} |")
    if oolong:
        sections.append(f"| OOLONG (synthetic) | — | {_fmt_score(oolong['avg_score'])} |")

    return "\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate RLM benchmark results")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=None, help="Output path (default: <input-dir>/RESULTS.md)")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output) if args.output else input_dir / "RESULTS.md"
    report = build_report(input_dir)
    output.write_text(report, encoding="utf-8")
    print(f"Report written to: {output}")
    print()
    print(report)


if __name__ == "__main__":
    main()
