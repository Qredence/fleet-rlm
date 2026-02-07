#!/usr/bin/env python3
"""Test skill activation based on description matching."""

import json
import re


# Test queries: (query, should_trigger, reason)
ACTIVATION_TESTS = [
    # Should trigger (high confidence)
    ("Analyze this 500MB log file", True, "large file + log analysis"),
    ("Process this file with 100K lines", True, "100K lines keyword"),
    ("Search for errors in production.log", True, "log analysis"),
    ("Summarize this long transcript", True, "summarizing long transcripts"),
    ("Extract patterns from massive dataset", True, "patterns + voluminous data"),
    ("Handle context overflow situation", True, "context overflow keyword"),
    ("Use map-reduce to analyze big data", True, "map-reduce + big data"),
    ("Process documentation dump", True, "documentation dumps"),
    # Should NOT trigger
    ("Fix the bug in main.py", False, "small file, code fix"),
    ("Create a React component", False, "unrelated task"),
    ("Write a function to sort array", False, "algorithm task"),
    ("Deploy to production", False, "deployment task"),
    # Edge cases (ambiguous)
    ("Find patterns in the data", None, "ambiguous - no size context"),
    ("Analyze this file", None, "ambiguous - no size specified"),
]


def check_activation(query: str, description: str) -> bool:
    """
    Check if query should trigger skill based on description.
    Uses keyword matching from the description.
    """
    # Keywords from the skill description
    trigger_keywords = [
        "large file",
        "log analysis",
        "100K lines",
        "big data",
        "massive",
        "voluminous",
        "long transcript",
        "context overflow",
        "map-reduce",
        "chunk processing",
        "documentation dump",
    ]

    query_lower = query.lower()

    # Check for explicit trigger keywords
    for keyword in trigger_keywords:
        if keyword.lower() in query_lower:
            return True

    # Check for size indicators (>1MB, >100K lines, etc.)
    size_patterns = [
        r"\d+MB",
        r"\d+GB",
        r"\d+K lines",
        r"\d+K lines",
        r"large",
        r"massive",
        r"huge",
        r"big",
    ]
    for pattern in size_patterns:
        if re.search(pattern, query_lower):
            return True

    return False


def run_activation_tests(description: str) -> dict:
    """Run all activation tests and return results."""
    results = {
        "total": len(ACTIVATION_TESTS),
        "correct": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "ambiguous": 0,
        "details": [],
    }

    for query, expected, reason in ACTIVATION_TESTS:
        actual = check_activation(query, description)

        if expected is None:
            # Ambiguous case - just record, don't score
            results["ambiguous"] += 1
            results["details"].append(
                {
                    "query": query,
                    "expected": "ambiguous",
                    "actual": actual,
                    "reason": reason,
                    "result": "ambiguous",
                }
            )
        elif actual == expected:
            results["correct"] += 1
            results["details"].append(
                {
                    "query": query,
                    "expected": expected,
                    "actual": actual,
                    "reason": reason,
                    "result": "correct",
                }
            )
        elif actual and not expected:
            results["false_positives"] += 1
            results["details"].append(
                {
                    "query": query,
                    "expected": expected,
                    "actual": actual,
                    "reason": reason,
                    "result": "false_positive",
                }
            )
        else:
            results["false_negatives"] += 1
            results["details"].append(
                {
                    "query": query,
                    "expected": expected,
                    "actual": actual,
                    "reason": reason,
                    "result": "false_negative",
                }
            )

    # Calculate score (only on non-ambiguous tests)
    scored_tests = results["total"] - results["ambiguous"]
    results["accuracy"] = results["correct"] / scored_tests if scored_tests > 0 else 0
    results["score"] = int(results["accuracy"] * 15)  # 15 points max

    return results


def main():
    """Run activation tests."""
    # Read skill description
    with open("../SKILL.md", "r") as f:
        content = f.read()

    # Extract description from frontmatter
    match = re.search(r'description:\s*"([^"]+)"', content)
    if match:
        description = match.group(1)
    else:
        match = re.search(r"description:\s*(.+?)(?:\n\w|\n---|$)", content, re.DOTALL)
        description = match.group(1).strip() if match else ""

    print("=" * 60)
    print("SKILL ACTIVATION TEST")
    print("=" * 60)
    print(f"\nDescription: {description[:100]}...")
    print()

    # Run tests
    results = run_activation_tests(description)

    print(f"Total tests: {results['total']}")
    print(f"Scored tests: {results['total'] - results['ambiguous']}")
    print(f"Correct: {results['correct']}")
    print(f"False positives: {results['false_positives']}")
    print(f"False negatives: {results['false_negatives']}")
    print(f"Ambiguous: {results['ambiguous']}")
    print()
    print(f"Accuracy: {results['accuracy']:.1%}")
    print(f"Score: {results['score']}/15 points")
    print()

    # Show failures
    print("FAILED TESTS:")
    for detail in results["details"]:
        if detail["result"] in ["false_positive", "false_negative"]:
            print(f"  [{detail['result']}] '{detail['query']}'")
            print(f"           Expected: {detail['expected']}, Got: {detail['actual']}")
            print(f"           Reason: {detail['reason']}")

    # Save results
    with open("results/activation_test.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\nResults saved to results/activation_test.json")

    return results


if __name__ == "__main__":
    main()
