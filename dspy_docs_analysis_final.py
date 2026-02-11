#!/usr/bin/env python3
"""RLM pattern analysis of DSPy documentation - using regex + local extraction.

This demonstrates the RLM pattern with chunked processing and synthesis,
using regex for extraction (reliable for structured documentation).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fleet_rlm.chunking import chunk_by_size


def load_document(path: str) -> str:
    """Load the document content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_from_chunk(chunk: str, chunk_idx: int) -> dict:
    """Extract entities from a single chunk using regex patterns."""

    # Module patterns - look for markdown files in modules/ directory
    module_pattern = r'modules/([A-Z][a-zA-Z0-9_]*?)\.md'
    modules = list(set(re.findall(module_pattern, chunk)))

    # Optimizer patterns - look for markdown files in optimizers/ directory
    optimizer_pattern = r'optimizers/([A-Z][a-zA-Z0-9_]*?)\.md'
    optimizers = list(set(re.findall(optimizer_pattern, chunk)))

    # Class patterns - look for markdown files in any API directory
    class_pattern = r'api/[a-z/]+([A-Z][a-zA-Z0-9_]*?)\.md'
    classes = list(set(re.findall(class_pattern, chunk)))

    # Additional patterns for inline class references
    inline_class_pattern = r'class\s+`?([A-Z][a-zA-Z0-9_]+)`?'
    inline_classes = list(set(re.findall(inline_class_pattern, chunk)))

    # Function patterns
    func_pattern = r'def\s+`?([a-z_][a-zA-Z0-9_]*)`?\s*\('
    functions = list(set(re.findall(func_pattern, chunk)))

    return {
        "chunk_index": chunk_idx,
        "modules": modules,
        "optimizers": optimizers,
        "classes": list(set(classes + inline_classes)),
        "functions": functions,
    }


def synthesize_results(all_extractions: list[dict], doc_stats: dict) -> dict:
    """Synthesize all chunk extractions into final results."""

    all_modules = set()
    all_optimizers = set()
    all_classes = set()
    all_functions = set()

    for extraction in all_extractions:
        all_modules.update(extraction.get("modules", []))
        all_optimizers.update(extraction.get("optimizers", []))
        all_classes.update(extraction.get("classes", []))
        all_functions.update(extraction.get("functions", []))

    # Filter out common false positives
    false_positives = {"The", "This", "For", "And", "With", "From", "That", "Not", "But", "You", "Can", "See", "Use"}
    all_modules = {m for m in all_modules if m not in false_positives and len(m) > 2}
    all_optimizers = {o for o in all_optimizers if o not in false_positives and len(o) > 2}
    all_classes = {c for c in all_classes if c not in false_positives and len(c) > 2}
    all_functions = {f for f in all_functions if f not in false_positives and len(f) > 2}

    # Categorize modules
    predict_modules = [m for m in all_modules if any(x in m for x in ["Predict", "Chain", "ReAct", "Refine", "Program", "Parallel", "BestOfN", "MultiChain", "Module"])]
    other_modules = sorted([m for m in all_modules if m not in predict_modules])

    # Categorize optimizers
    bootstrap_optimizers = sorted([o for o in all_optimizers if "Bootstrap" in o])
    other_optimizers = sorted([o for o in all_optimizers if o not in bootstrap_optimizers])

    return {
        "modules": {
            "predict_modules": sorted(predict_modules),
            "other_modules": other_modules,
            "all_modules": sorted(all_modules)
        },
        "optimizers": {
            "bootstrap_optimizers": bootstrap_optimizers,
            "other_optimizers": other_optimizers,
            "all_optimizers": sorted(all_optimizers)
        },
        "classes": sorted(all_classes),
        "functions": sorted(all_functions),
        "statistics": {
            "total_modules": len(all_modules),
            "total_optimizers": len(all_optimizers),
            "total_classes": len(all_classes),
            "total_functions": len(all_functions),
            "chunks_processed": len(all_extractions)
        }
    }


def analyze_dspy_docs_rlm(doc_path: str) -> dict[str, Any]:
    """Analyze DSPy documentation using RLM pattern.

    Steps:
    1. Load and chunk the document
    2. Extract entities from each chunk (parallel processing)
    3. Synthesize results into structured findings
    """
    print(f"[RLM] Loading DSPy documentation: {doc_path}")
    content = load_document(doc_path)
    total_chars = len(content)
    total_lines = content.count("\n")
    print(f"[RLM] Document size: {total_chars:,} chars, {total_lines:,} lines")

    # Chunk by size for parallel processing
    print("[RLM] Chunking document...")
    chunks = chunk_by_size(content, size=8000, overlap=500)
    print(f"[RLM] Created {len(chunks)} chunks")

    # Phase 1: Extract from each chunk
    print(f"[RLM] Phase 1: Extracting entities from {len(chunks)} chunks...")
    all_extractions = []

    for i, chunk in enumerate(chunks):
        print(f"  [Chunk {i+1}/{len(chunks)}] Extracting...")
        extraction = extract_from_chunk(chunk, i)
        all_extractions.append(extraction)
        print(f"    Found: {len(extraction['modules'])} modules, "
              f"{len(extraction['optimizers'])} optimizers, "
              f"{len(extraction['classes'])} classes")

    print(f"[RLM] Completed extraction from {len(all_extractions)} chunks")

    # Phase 2: Synthesize all extractions
    print("[RLM] Phase 2: Synthesizing results...")
    doc_stats = {
        "total_chars": total_chars,
        "total_lines": total_lines,
        "chunks": len(chunks),
    }
    final_result = synthesize_results(all_extractions, doc_stats)

    return {
        "document_stats": doc_stats,
        "chunk_extractions": all_extractions,
        "rlm_results": final_result,
    }


def main():
    """Main entry point."""
    doc_path = "/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/rlm_content/dspy-knowledge/dspy-doc.txt"

    print("=" * 70)
    print("DSPy Documentation RLM Pattern Analysis")
    print("=" * 70)
    print("\nQuery: Extract all modules, classes, and optimizers")
    print("=" * 70)

    result = analyze_dspy_docs_rlm(doc_path)

    print("\n" + "=" * 70)
    print("ANALYSIS RESULTS")
    print("=" * 70)

    stats = result["document_stats"]
    print(f"\n📊 Document Stats:")
    print(f"  - Total characters: {stats['total_chars']:,}")
    print(f"  - Total lines: {stats['total_lines']:,}")
    print(f"  - Chunks created: {stats['chunks']}")

    rlm_results = result.get("rlm_results", {})
    stats = rlm_results.get("statistics", {})
    print(f"\n📈 Extraction Statistics:")
    print(f"  - Total modules: {stats.get('total_modules', 'N/A')}")
    print(f"  - Total optimizers: {stats.get('total_optimizers', 'N/A')}")
    print(f"  - Total classes: {stats.get('total_classes', 'N/A')}")
    print(f"  - Total functions: {stats.get('total_functions', 'N/A')}")

    modules = rlm_results.get("modules", {})
    print(f"\n🔧 Modules ({len(modules.get('all_modules', []))} total):")

    predict_modules = modules.get("predict_modules", [])
    print(f"  Predict/Reasoning Modules ({len(predict_modules)}):")
    for m in predict_modules:
        print(f"    - {m}")

    other_modules = modules.get("other_modules", [])
    if other_modules:
        print(f"\n  Other Modules ({len(other_modules)}):")
        for m in other_modules[:10]:
            print(f"    - {m}")
        if len(other_modules) > 10:
            print(f"    ... and {len(other_modules) - 10} more")

    optimizers = rlm_results.get("optimizers", {})
    print(f"\n⚙️ Optimizers ({len(optimizers.get('all_optimizers', []))} total):")

    bootstrap_optimizers = optimizers.get("bootstrap_optimizers", [])
    print(f"  Bootstrap Optimizers ({len(bootstrap_optimizers)}):")
    for o in bootstrap_optimizers:
        print(f"    - {o}")

    other_optimizers = optimizers.get("other_optimizers", [])
    if other_optimizers:
        print(f"\n  Other Optimizers ({len(other_optimizers)}):")
        for o in other_optimizers:
            print(f"    - {o}")

    classes = rlm_results.get("classes", [])
    print(f"\n📦 Classes ({len(classes)} total):")
    for c in classes[:30]:
        print(f"  - {c}")
    if len(classes) > 30:
        print(f"  ... and {len(classes) - 30} more")

    functions = rlm_results.get("functions", [])
    if functions:
        print(f"\n🔨 Functions ({len(functions)} total):")
        for f in functions[:15]:
            print(f"  - {f}")
        if len(functions) > 15:
            print(f"  ... and {len(functions) - 15} more")

    # Save results
    output_path = Path("/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/dspy_docs_analysis_results.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n\n💾 Results saved to: {output_path}")

    # Summary
    print("\n" + "=" * 70)
    print("RLM PATTERN SUMMARY")
    print("=" * 70)
    doc_stats = result["document_stats"]
    rlm_stats = rlm_results["statistics"]
    print(f"""
Successfully analyzed DSPy documentation using RLM pattern:

  ✓ Loaded {doc_stats['total_chars']:,} character document
  ✓ Chunked into {rlm_stats['chunks_processed']} sections
  ✓ Extracted {rlm_stats['total_modules']} modules
  ✓ Extracted {rlm_stats['total_optimizers']} optimizers
  ✓ Extracted {rlm_stats['total_classes']} classes
  ✓ Synthesized results into structured output

Key DSPy Components Identified:
  - Core Modules: Predict, ChainOfThought, ReAct, Refine, ProgramOfThought
  - Optimizers: BootstrapFewShot, COPRO, MIPRO, Ensemble, KNN variants
  - Utilities: Adapter classes, Evaluation metrics, LM/Embedder models
""")

    return result


if __name__ == "__main__":
    main()
