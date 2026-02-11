#!/usr/bin/env python3
"""RLM pattern analysis of the RLM paper - demonstrates recursive long-context processing."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import dspy
from fleet_rlm.interpreter import ModalInterpreter
from fleet_rlm.chunking import chunk_by_headers


def load_document(path: str) -> str:
    """Load the document content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def analyze_document_rlm_pattern(doc_path: str) -> dict[str, Any]:
    """Analyze a large document using the RLM pattern.

    Steps:
    1. Load and chunk the document by headers
    2. Delegate semantic analysis to batched LLM queries
    3. Synthesize results into structured findings
    """
    # Load document
    print(f"[RLM] Loading document: {doc_path}")
    content = load_document(doc_path)
    total_chars = len(content)
    total_lines = content.count("\n")
    print(f"[RLM] Document size: {total_chars:,} chars, {total_lines:,} lines")

    # Chunk by headers
    print("[RLM] Chunking document by headers...")
    chunks = chunk_by_headers(content, max_chunk_size=8000)
    print(f"[RLM] Created {len(chunks)} chunks")

    # Initialize DSPy LM
    lm = dspy.LM(
        model=os.environ.get("DSPY_LM_MODEL", "openai/gpt-4o-mini"),
        api_key=os.environ.get("DSPY_LLM_API_KEY"),
        max_tokens=4000,
    )
    dspy.settings.configure(lm=lm)

    # Use ModalInterpreter for batched analysis
    print("[RLM] Initializing ModalInterpreter...")
    with ModalInterpreter(
        timeout=300,
        max_llm_calls=100,
        summarize_stdout=True,
    ) as interpreter:
        # Store document in sandbox
        print("[RLM] Storing document in sandbox...")
        interpreter.execute(f'''
document_content = """{content.replace('"', '\\"').replace("'''", '\\'\\'\\'')}"""
chunks = {json.dumps(chunks)}
print(f"Document loaded: {{len(document_content)}} chars")
print(f"Chunks: {{len(chunks)}}")
''')

        # Analyze each chunk with batched queries
        print("[RLM] Delegating semantic analysis to batched LLM queries...")

        analysis_prompts = []
        for i, chunk in enumerate(chunks[:10]):  # Analyze first 10 chunks
            prompt = f"""Analyze this section of the RLM paper and extract:
1. Main topics/concepts discussed
2. Key findings or contributions
3. Methodology details (if any)
4. Important equations or algorithms mentioned

SECTION {i+1}/{len(chunks)}:
{chunk[:3000]}...

Respond in JSON format:
{{
    "topics": ["topic1", "topic2"],
    "findings": ["finding1", "finding2"],
    "methodology": "description",
    "algorithms": ["algo1"],
    "importance": "high/medium/low"
}}"""
            analysis_prompts.append(prompt)

        # Execute batched analysis
        print(f"[RLM] Running {len(analysis_prompts)} parallel analyses...")
        code = '''
import json

results = []
for i, prompt in enumerate(analysis_prompts):
    result = llm_query(prompt, temperature=0.3)
    results.append({"chunk_index": i, "analysis": result})
    print(f"Chunk {i+1}/{len(analysis_prompts)} analyzed")

# Return structured results
final_output(results)
'''
        result = interpreter.execute(
            code,
            variables={"analysis_prompts": analysis_prompts},
            tool_names=["llm_query", "final_output"],
        )

        # Synthesize findings
        print("[RLM] Synthesizing results...")
        chunk_analyses = result if isinstance(result, list) else []

        synthesis_prompt = f"""Synthesize these chunk analyses into a coherent summary:

CHUNK ANALYSES:
{json.dumps(chunk_analyses, indent=2)}

Provide:
1. Overall document structure and organization
2. Key themes and patterns across sections
3. Main contributions of the paper
4. Technical innovations described
5. Experimental results highlights
6. Recommendations for further reading

Respond in JSON format."""

        synthesis_code = f'''
synthesis = llm_query("""{synthesis_prompt.replace('"', '\\"')}""", temperature=0.2)
final_output({{
    "document_stats": {{
        "total_chars": {total_chars},
        "total_lines": {total_lines},
        "chunks_analyzed": {len(chunk_analyses)},
        "total_chunks": {len(chunks)}
    }},
    "chunk_analyses": chunk_analyses,
    "synthesis": synthesis
}})
'''
        final_result = interpreter.execute(
            synthesis_code,
            tool_names=["llm_query", "final_output"],
        )

        return final_result


def main():
    """Main entry point."""
    doc_path = "/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/rlm_content/rlm-knowledge/rlm-paper.md"

    print("=" * 60)
    print("RLM Pattern Document Analysis")
    print("=" * 60)

    try:
        result = analyze_document_rlm_pattern(doc_path)

        print("\n" + "=" * 60)
        print("ANALYSIS RESULTS")
        print("=" * 60)

        if isinstance(result, dict):
            stats = result.get("document_stats", {})
            print(f"\nDocument Stats:")
            print(f"  - Total characters: {stats.get('total_chars', 'N/A'):,}")
            print(f"  - Total lines: {stats.get('total_lines', 'N/A'):,}")
            print(f"  - Chunks analyzed: {stats.get('chunks_analyzed', 'N/A')}")
            print(f"  - Total chunks: {stats.get('total_chunks', 'N/A')}")

            synthesis = result.get("synthesis", {})
            if isinstance(synthesis, str):
                try:
                    synthesis = json.loads(synthesis)
                except json.JSONDecodeError:
                    pass

            print(f"\nSynthesis:")
            if isinstance(synthesis, dict):
                for key, value in synthesis.items():
                    print(f"\n  {key.upper()}:")
                    if isinstance(value, list):
                        for item in value:
                            print(f"    - {item}")
                    else:
                        print(f"    {value}")
            else:
                print(f"  {synthesis}")

        # Save results
        output_path = Path("/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/rlm_analysis_results.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n\nResults saved to: {output_path}")

    except Exception as e:
        print(f"\n[ERROR] Analysis failed: {e}")
        raise


if __name__ == "__main__":
    main()
