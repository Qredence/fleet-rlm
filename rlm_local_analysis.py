#!/usr/bin/env python3
"""Local RLM pattern analysis of the RLM paper - demonstrates recursive long-context processing."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import dspy


def load_document(path: str) -> str:
    """Load the document content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def chunk_by_headers_local(content: str, max_chunk_size: int = 8000) -> list[str]:
    """Chunk document by markdown headers."""
    # Split by headers (lines starting with #)
    lines = content.split("\n")
    chunks = []
    current_chunk = []
    current_size = 0

    for line in lines:
        # Check if this is a header
        is_header = re.match(r"^#{1,6}\s", line)

        if is_header and current_chunk and current_size > 1000:
            # Save current chunk and start new one
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_size = len(line)
        else:
            current_chunk.append(line)
            current_size += len(line) + 1

        # Force chunk if too large
        if current_size > max_chunk_size and len(current_chunk) > 10:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_size = 0

    # Add remaining content
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


class ChunkAnalyzer(dspy.Signature):
    """Analyze a document chunk and extract key information."""

    chunk_text: str = dspy.InputField(desc="The document chunk to analyze")
    chunk_index: int = dspy.InputField(desc="Index of this chunk")
    total_chunks: int = dspy.InputField(desc="Total number of chunks")

    topics: list[str] = dspy.OutputField(desc="Main topics/concepts in this chunk")
    findings: list[str] = dspy.OutputField(desc="Key findings or contributions")
    methodology: str = dspy.OutputField(desc="Methodology details if present")
    importance: str = dspy.OutputField(desc="Importance level: high/medium/low")


class SynthesisAnalyzer(dspy.Signature):
    """Synthesize chunk analyses into coherent summary."""

    chunk_analyses: list[dict] = dspy.InputField(desc="List of chunk analysis results")
    document_stats: dict = dspy.InputField(desc="Document statistics")

    overall_structure: str = dspy.OutputField(desc="Document structure and organization")
    key_themes: list[str] = dspy.OutputField(desc="Key themes across sections")
    main_contributions: list[str] = dspy.OutputField(desc="Main contributions")
    technical_innovations: list[str] = dspy.OutputField(desc="Technical innovations")
    recommendations: list[str] = dspy.OutputField(desc="Recommendations for further reading")


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
    chunks = chunk_by_headers_local(content, max_chunk_size=6000)
    print(f"[RLM] Created {len(chunks)} chunks")

    # Initialize DSPy LM
    lm = dspy.LM(
        model=os.environ.get("DSPY_LM_MODEL", "openai/gpt-4o-mini"),
        api_key=os.environ.get("DSPY_LLM_API_KEY"),
        max_tokens=2000,
    )
    dspy.settings.configure(lm=lm)

    # Analyze chunks
    print(f"[RLM] Analyzing {len(chunks)} chunks...")
    analyzer = dspy.ChainOfThought(ChunkAnalyzer)

    chunk_analyses = []
    for i, chunk in enumerate(chunks[:8]):  # Analyze first 8 chunks
        print(f"  [Chunk {i+1}/{min(len(chunks), 8)}] Analyzing...")
        try:
            result = analyzer(
                chunk_text=chunk[:4000],
                chunk_index=i,
                total_chunks=len(chunks),
            )
            chunk_analyses.append({
                "chunk_index": i,
                "topics": result.topics,
                "findings": result.findings,
                "methodology": result.methodology,
                "importance": result.importance,
            })
        except Exception as e:
            print(f"    Warning: Analysis failed for chunk {i}: {e}")
            chunk_analyses.append({
                "chunk_index": i,
                "topics": [],
                "findings": ["Analysis failed"],
                "methodology": "",
                "importance": "low",
            })

    # Synthesize results
    print("[RLM] Synthesizing results...")
    synthesizer = dspy.ChainOfThought(SynthesisAnalyzer)

    try:
        synthesis = synthesizer(
            chunk_analyses=chunk_analyses,
            document_stats={
                "total_chars": total_chars,
                "total_lines": total_lines,
                "chunks_analyzed": len(chunk_analyses),
                "total_chunks": len(chunks),
            },
        )
    except Exception as e:
        print(f"  Warning: Synthesis failed: {e}")
        synthesis = None

    return {
        "document_stats": {
            "total_chars": total_chars,
            "total_lines": total_lines,
            "chunks_analyzed": len(chunk_analyses),
            "total_chunks": len(chunks),
        },
        "chunk_analyses": chunk_analyses,
        "synthesis": {
            "overall_structure": synthesis.overall_structure if synthesis else "N/A",
            "key_themes": synthesis.key_themes if synthesis else [],
            "main_contributions": synthesis.main_contributions if synthesis else [],
            "technical_innovations": synthesis.technical_innovations if synthesis else [],
            "recommendations": synthesis.recommendations if synthesis else [],
        } if synthesis else None,
    }


def main():
    """Main entry point."""
    doc_path = "/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/rlm_content/rlm-knowledge/rlm-paper.md"

    print("=" * 60)
    print("RLM Pattern Document Analysis (Local Mode)")
    print("=" * 60)

    try:
        result = analyze_document_rlm_pattern(doc_path)

        print("\n" + "=" * 60)
        print("ANALYSIS RESULTS")
        print("=" * 60)

        stats = result.get("document_stats", {})
        print(f"\n📊 Document Stats:")
        print(f"  - Total characters: {stats.get('total_chars', 'N/A'):,}")
        print(f"  - Total lines: {stats.get('total_lines', 'N/A'):,}")
        print(f"  - Chunks analyzed: {stats.get('chunks_analyzed', 'N/A')}")
        print(f"  - Total chunks: {stats.get('total_chunks', 'N/A')}")

        synthesis = result.get("synthesis")
        if synthesis:
            print(f"\n📋 Overall Structure:")
            print(f"  {synthesis.get('overall_structure', 'N/A')}")

            print(f"\n🎯 Key Themes:")
            for theme in synthesis.get('key_themes', []):
                print(f"  - {theme}")

            print(f"\n💡 Main Contributions:")
            for contrib in synthesis.get('main_contributions', []):
                print(f"  - {contrib}")

            print(f"\n🔬 Technical Innovations:")
            for innovation in synthesis.get('technical_innovations', []):
                print(f"  - {innovation}")

            print(f"\n📚 Recommendations:")
            for rec in synthesis.get('recommendations', []):
                print(f"  - {rec}")

        # Show chunk-level findings
        print(f"\n📑 Chunk-Level Findings:")
        for analysis in result.get("chunk_analyses", []):
            idx = analysis.get("chunk_index", 0)
            importance = analysis.get("importance", "unknown")
            topics = analysis.get("topics", [])
            print(f"\n  Chunk {idx+1} [{importance.upper()}]:")
            print(f"    Topics: {', '.join(topics[:3]) if topics else 'N/A'}")
            for finding in analysis.get("findings", [])[:2]:
                print(f"    - {finding}")

        # Save results
        output_path = Path("/Users/zocho/.codex/worktrees/396e/fleet-rlm-dspy/rlm_analysis_results.json")
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
