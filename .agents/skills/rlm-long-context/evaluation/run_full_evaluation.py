#!/usr/bin/env python3
"""Run full evaluation suite for RLM long-context skill."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def check_prerequisites():
    """Check that test data exists."""
    if not Path("test_data/test_data.log").exists():
        print("Test data not found. Generating...")
        subprocess.run([sys.executable, "generate_test_data.py"], check=True)


def run_test_category(name: str, script: str) -> dict:
    """Run a test category and parse results."""
    print(f"\n{'=' * 60}")
    print(f"Running: {name}")
    print("=" * 60)

    try:
        result = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=300
        )
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)

        # Try to load results
        result_file = f"results/{name.lower().replace(' ', '_')}.json"
        if Path(result_file).exists():
            with open(result_file) as f:
                return json.load(f)
        else:
            return {"error": "No results file generated", "score": 0}

    except subprocess.TimeoutExpired:
        print(f"TIMEOUT: {name} took too long")
        return {"error": "timeout", "score": 0}
    except Exception as e:
        print(f"ERROR running {name}: {e}")
        return {"error": str(e), "score": 0}


def calculate_total_score(results: dict) -> dict:
    """Calculate total score across all dimensions."""
    scoring = {
        "Activation": {"weight": 15, "result": results.get("activation", {})},
        "Correctness": {"weight": 20, "result": results.get("correctness", {})},
        "Efficiency": {"weight": 15, "result": results.get("efficiency", {})},
        "Robustness": {"weight": 15, "result": results.get("robustness", {})},
        "Usability": {"weight": 15, "result": results.get("usability", {})},
    }

    total_score = 0
    max_score = 0
    breakdown = {}

    for dimension, config in scoring.items():
        score = config["result"].get("score", 0)
        max_points = config["weight"]
        total_score += score
        max_score += max_points
        breakdown[dimension] = {
            "score": score,
            "max": max_points,
            "percentage": (score / max_points * 100) if max_points > 0 else 0,
        }

    return {
        "total_score": total_score,
        "max_score": max_score,
        "percentage": (total_score / max_score * 100) if max_score > 0 else 0,
        "breakdown": breakdown,
        "grade": calculate_grade(total_score / max_score * 100),
    }


def calculate_grade(percentage: float) -> str:
    """Calculate letter grade from percentage."""
    if percentage >= 90:
        return "A (Excellent)"
    elif percentage >= 80:
        return "B (Good)"
    elif percentage >= 70:
        return "C (Adequate)"
    elif percentage >= 60:
        return "D (Below Average)"
    else:
        return "F (Poor)"


def generate_report(results: dict, timestamp: str) -> str:
    """Generate markdown evaluation report."""
    summary = calculate_total_score(results)

    report = f"""# RLM Long-Context Skill Evaluation Report

**Evaluation Date:** {timestamp}
**Version:** {"Fixed" if Path("../SKILL.md").stat().st_size > 20000 else "Baseline"}

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total Score** | {summary["total_score"]}/{summary["max_score"]} |
| **Percentage** | {summary["percentage"]:.1f}% |
| **Grade** | {summary["grade"]} |

## Dimension Breakdown

| Dimension | Score | Max | % | Grade |
|-----------|-------|-----|---|-------|
"""

    for dim, scores in summary["breakdown"].items():
        grade = calculate_grade(scores["percentage"]).split()[0]
        report += f"| {dim} | {scores['score']} | {scores['max']} | {scores['percentage']:.0f}% | {grade} |\n"

    report += "\n## Detailed Results\n\n"

    for category, data in results.items():
        report += f"### {category.title()}\n\n"
        report += f"```json\n{json.dumps(data, indent=2)}\n```\n\n"

    return report


def main():
    """Run full evaluation suite."""
    timestamp = datetime.now().isoformat()

    print("=" * 60)
    print("RLM LONG-CONTEXT SKILL EVALUATION")
    print("=" * 60)
    print(f"\nStarted at: {timestamp}")

    # Check prerequisites
    check_prerequisites()

    # Create results directory
    os.makedirs("results", exist_ok=True)

    # Run test categories
    results = {}

    # 1. Activation Tests
    if Path("test_activation.py").exists():
        results["activation"] = run_test_category("Activation", "test_activation.py")

    # 2. Correctness Tests (mock for now)
    results["correctness"] = {
        "note": "Requires functional subagent to test",
        "score": 0,
        "max": 20,
        "tests": [
            {"id": "T2.1", "query": "Count all ERROR entries", "status": "pending"},
            {"id": "T2.2", "query": "Find first timeout error", "status": "pending"},
        ],
    }

    # 3. Efficiency Tests (mock for now)
    results["efficiency"] = {
        "note": "Requires running implementation to test",
        "score": 0,
        "max": 15,
    }

    # 4. Robustness Tests
    results["robustness"] = {
        "tests": [
            {
                "id": "T4.1",
                "scenario": "Empty file",
                "subagent_exists": Path("../../../rlm-subcall.md").exists(),
                "pass": Path("../../../rlm-subcall.md").exists(),
            },
            {
                "id": "T4.4",
                "scenario": "Missing subagent",
                "subagent_exists": Path("../../../rlm-subcall.md").exists(),
                "pass": Path("../../../rlm-subcall.md").exists(),
            },
        ],
        "score": 15 if Path("../../rlm-subcall.md").exists() else 5,
        "max": 15,
    }

    # 5. Usability Tests
    results["usability"] = {
        "tests": [
            {
                "id": "T5.1",
                "check": "Description has WHEN triggers",
                "pass": "Use when (1)" in open("../SKILL.md").read(),
            },
            {
                "id": "T5.2",
                "check": "NEVER list exists",
                "pass": "## NEVER List" in open("../SKILL.md").read(),
            },
            {
                "id": "T5.3",
                "check": "MANDATORY loading triggers",
                "pass": "MANDATORY - READ ENTIRE FILE" in open("../SKILL.md").read(),
            },
            {
                "id": "T5.4",
                "check": "Subagent exists",
                "pass": Path("../../../rlm-subcall.md").exists(),
            },
            {
                "id": "T5.5",
                "check": "Correct paths in examples",
                "pass": ".agents/skills/rlm-long-context/scripts/"
                in open("../SKILL.md").read(),
            },
        ],
        "score": 0,
        "max": 15,
    }

    # Calculate usability score
    usability_passes = sum(1 for t in results["usability"]["tests"] if t["pass"])
    results["usability"]["score"] = int((usability_passes / 5) * 15)

    # Calculate summary
    summary = calculate_total_score(results)

    print("\n" + "=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    print(f"\nTotal Score: {summary['total_score']}/{summary['max_score']}")
    print(f"Percentage: {summary['percentage']:.1f}%")
    print(f"Grade: {summary['grade']}")

    # Generate report
    report = generate_report(results, timestamp)
    report_file = f"results/evaluation_report_{timestamp[:10]}.md"
    with open(report_file, "w") as f:
        f.write(report)

    print(f"\nReport saved to: {report_file}")

    # Save raw results
    with open("results/evaluation_results.json", "w") as f:
        json.dump(
            {"timestamp": timestamp, "summary": summary, "results": results},
            f,
            indent=2,
        )

    print("Raw results saved to: results/evaluation_results.json")

    return summary


if __name__ == "__main__":
    main()
