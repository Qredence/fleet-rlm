#!/usr/bin/env python3
"""RLM pattern analysis of DSPy documentation using ModalInterpreter.

Extracts modules, classes, and optimizers from the DSPy documentation.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import dspy
from fleet_rlm.interpreter import ModalInterpreter
from fleet_rlm.chunking import chunk_by_size


def load_document(path: str) -> str:
    """Load the document content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_with_regex(content: str) -> dict:
    """Pre-extract patterns using regex for validation."""
    # Module patterns - look for markdown files in modules/ directory
    module_pattern = r'modules/([A-Z][a-zA-Z0-9_]*?)\.md'
    modules = list(set(re.findall(module_pattern, content)))

    # Optimizer patterns - look for markdown files in optimizers/ directory
    optimizer_pattern = r'optimizers/([A-Z][a-zA-Z0-9_]*?)\.md'
    optimizers = list(set(re.findall(optimizer_pattern, content)))

    # Class patterns - look for markdown files in any API directory
    class_pattern = r'api/[a-z/]+([A-Z][a-zA-Z0-9_]*?)\.md'
    classes = list(set(re.findall(class_pattern, content)))

    return {
        "modules": sorted(modules),
        "optimizers": sorted(optimizers),
        "classes": sorted(classes),
    }


def analyze_dspy_docs_rlm(doc_path: str) -> dict[str, Any]:
    """Analyze DSPy documentation using RLM pattern with ModalInterpreter.

    Steps:
    1. Load and chunk the document
    2. Use ModalInterpreter with llm_query_batched for parallel extraction
    3. Synthesize results into structured findings
    """
    print(f"[RLM] Loading DSPy documentation: {doc_path}")
    content = load_document(doc_path)
    total_chars = len(content)
    total_lines = content.count("\n")
    print(f"[RLM] Document size: {total_chars:,} chars, {total_lines:,} lines")

    # Pre-extract with regex for reference
    print("[RLM] Pre-extracting patterns with regex...")
    regex_results = extract_with_regex(content)
    print(f"  Found {len(regex_results['modules'])} potential modules")
    print(f"  Found {len(regex_results['optimizers'])} potential optimizers")
    print(f"  Found {len(regex_results['classes'])} potential classes")

    # Chunk by size for parallel processing
    print("[RLM] Chunking document...")
    chunks = chunk_by_size(content, size=8000, overlap=500)
    print(f"[RLM] Created {len(chunks)} chunks")

    # Use ModalInterpreter for batched analysis
    print("[RLM] Initializing ModalInterpreter...")
    with ModalInterpreter(
        timeout=600,
        max_llm_calls=100,
        summarize_stdout=True,
    ) as interpreter:
        # Store chunks in sandbox
        print("[RLM] Loading chunks into sandbox...")

        # Prepare chunk data for sandbox
        chunk_data = []
        for i, chunk in enumerate(chunks):
            chunk_data.append({
                "index": i,
                "text": chunk[:6000],  # Limit chunk size
            })

        # Store in sandbox via variables
        interpreter.execute("chunks = []", variables={"chunks": chunk_data})
        interpreter.execute("print(f'Loaded {len(chunks)} chunks into sandbox')")

        # Phase 1: Extract from each chunk using batched queries
        print(f"[RLM] Phase 1: Extracting entities from {len(chunks)} chunks...")

        batch_size = 3
        all_extractions = []

        for batch_start in range(0, len(chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(chunks))
            print(f"  Processing batch {batch_start//batch_size + 1}/{(len(chunks) + batch_size - 1)//batch_size} (chunks {batch_start+1}-{batch_end})...")

            # Build extraction code for this batch
            extraction_code = f'''
import json

batch_results = []
for chunk_info in chunks[{batch_start}:{batch_end}]:
    chunk_idx = chunk_info["index"]
    chunk_text = chunk_info["text"]

    prompt = f"""Analyze this DSPy documentation chunk and extract ALL mentioned:

1. **Modules** (classes like Predict, ChainOfThought, ReAct, Refine, Parallel, ProgramOfThought, BestOfN, MultiChainComparison, Module)
2. **Optimizers** (classes like BootstrapFewShot, COPRO, MIPRO, BootstrapFinetune, Ensemble, KNN, InferRules, BetterTogether, LabeledFewShot, BootstrapRS, KNNFewShot)
3. **Classes** (any API classes mentioned in the documentation)
4. **Functions** (important functions or methods)

Look for class names, file names ending in .md, and API references.

CHUNK TEXT:
{{chunk_text[:4000]}}

Respond ONLY in JSON format:
{{
    "modules": ["ModuleName1", "ModuleName2"],
    "optimizers": ["OptimizerName1", "OptimizerName2"],
    "classes": ["ClassName1", "ClassName2"],
    "functions": ["function_name1", "function_name2"]
}}"""

    try:
        result = llm_query(prompt, temperature=0.1)
        # Try to parse JSON
        try:
            parsed = json.loads(result)
        except:
            # Extract JSON from markdown if wrapped
            json_match = result.strip()
            if "```json" in result:
                json_match = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                json_match = result.split("```")[1].split("```")[0]
            parsed = json.loads(json_match)

        batch_results.append({{
            "chunk_index": chunk_idx,
            "modules": parsed.get("modules", []),
            "optimizers": parsed.get("optimizers", []),
            "classes": parsed.get("classes", []),
            "functions": parsed.get("functions", [])
        }})
    except Exception as e:
        batch_results.append({{
            "chunk_index": chunk_idx,
            "error": str(e),
            "modules": [],
            "optimizers": [],
            "classes": [],
            "functions": []
        }})

final_output(batch_results)
'''
            try:
                result = interpreter.execute(extraction_code)
                if isinstance(result, list):
                    all_extractions.extend(result)
                    print(f"    Extracted from {len(result)} chunks")
                else:
                    print(f"    Warning: Unexpected result type: {type(result)}")
            except Exception as e:
                print(f"    Warning: Batch failed: {e}")

        print(f"[RLM] Completed extraction from {len(all_extractions)} chunks")

        # Phase 2: Synthesize all extractions
        print("[RLM] Phase 2: Synthesizing results...")

        synthesis_code = '''
# Aggregate all extractions
all_modules = set()
all_optimizers = set()
all_classes = set()
all_functions = set()

for extraction in all_extractions:
    if "error" not in extraction:
        all_modules.update(extraction.get("modules", []))
        all_optimizers.update(extraction.get("optimizers", []))
        all_classes.update(extraction.get("classes", []))
        all_functions.update(extraction.get("functions", []))

# Filter out common false positives
false_positives = {"The", "This", "For", "And", "With", "From", "That", "Not", "But", "You", "Can", "See", "Use", "Get", "Set", "Add", "New", "One", "Two"}
all_modules = {m for m in all_modules if m not in false_positives and len(m) > 2}
all_optimizers = {o for o in all_optimizers if o not in false_positives and len(o) > 2}
all_classes = {c for c in all_classes if c not in false_positives and len(c) > 2}
all_functions = {f for f in all_functions if f not in false_positives and len(f) > 2}

# Categorize modules
predict_modules = [m for m in all_modules if any(x in m for x in ["Predict", "Chain", "ReAct", "Refine", "Program", "Parallel", "BestOfN", "MultiChain", "Module"])]
other_modules = [m for m in all_modules if m not in predict_modules]

# Categorize optimizers
bootstrap_optimizers = [o for o in all_optimizers if "Bootstrap" in o]
other_optimizers = [o for o in all_optimizers if o not in bootstrap_optimizers]

final_output({
    "modules": {
        "predict_modules": sorted(predict_modules),
        "other_modules": sorted(other_modules),
        "all_modules": sorted(all_modules)
    },
    "optimizers": {
        "bootstrap_optimizers": sorted(bootstrap_optimizers),
        "other_optimizers": sorted(other_optimizers),
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
})
'''
        final_result = interpreter.execute(
            synthesis_code,
            variables={"all_extractions": all_extractions}
        )

        return {
            "document_stats": {
                "total_chars": total_chars,
                "total_lines": total_lines,
                "chunks": len(chunks),
                "chunks_processed": len(all_extractions),
            },
            "regex_baseline": regex_results,
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

    try:
        result = analyze_dspy_docs_rlm(doc_path)

        print("\n" + "=" * 70)
        print("ANALYSIS RESULTS")
        print("=" * 70)

        stats = result["document_stats"]
        print(f"\n📊 Document Stats:")
        print(f"  - Total characters: {stats['total_chars']:,}")
        print(f"  - Total lines: {stats['total_lines']:,}")
        print(f"  - Chunks created: {stats['chunks']}")
        print(f"  - Chunks processed: {stats['chunks_processed']}")

        rlm_results = result.get("rlm_results", {})

        if isinstance(rlm_results, dict):
            stats = rlm_results.get("statistics", {})
            print(f"\n📈 Extraction Statistics:")
            print(f"  - Total modules: {stats.get('total_modules', 'N/A')}")
            print(f"  - Total optimizers: {stats.get('total_optimizers', 'N/A')}")
            print(f"  - Total classes: {stats.get('total_classes', 'N/A')}")
            print(f"  - Total functions: {stats.get('total_functions', 'N/A')}")

            modules = rlm_results.get("modules", {})
            print(f"\n🔧 Modules ({len(modules.get('all_modules', []))} total):")
            print(f"  Predict/Reasoning Modules:")
            for m in modules.get("predict_modules", [])[:15]:
                print(f"    - {m}")
            if len(modules.get("predict_modules", [])) > 15:
                print(f"    ... and {len(modules['predict_modules']) - 15} more")

            print(f"\n  Other Modules:")
            for m in modules.get("other_modules", [])[:10]:
                print(f"    - {m}")

            optimizers = rlm_results.get("optimizers", {})
            print(f"\n⚙️ Optimizers ({len(optimizers.get('all_optimizers', []))} total):")
            print(f"  Bootstrap Optimizers:")
            for o in optimizers.get("bootstrap_optimizers", []):
                print(f"    - {o}")
            print(f"  Other Optimizers:")
            for o in optimizers.get("other_optimizers", [])[:10]:
                print(f"    - {o}")

            classes = rlm_results.get("classes", [])
            print(f"\n📦 Classes ({len(classes)} total):")
            for c in classes[:20]:
                print(f"  - {c}")
            if len(classes) > 20:
                print(f"  ... and {len(classes) - 20} more")

        # Save results
        output_path = Path("/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/dspy_docs_analysis_results.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n\n💾 Results saved to: {output_path}")

        return result

    except Exception as e:
        print(f"\n[ERROR] Analysis failed: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
