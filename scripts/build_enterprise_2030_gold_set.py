#!/usr/bin/env python3
"""Build the Enterprise 2030 PDF needle-in-haystack evaluation gold set.

Extracts speaker quotes and section anchors from the PDF via MarkItDown ingestion,
then writes `.data/datasets/enterprise-2030-needle-eval.json`.

Usage:
    uv run python scripts/build_enterprise_2030_gold_set.py
    uv run python scripts/build_enterprise_2030_gold_set.py --pdf output/the-enterprise-in-2030-report-copy.pdf
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fleet_rlm.runtime.content.ingestion import read_document_content  # noqa: E402

DEFAULT_PDF = ROOT / "output" / "the-enterprise-in-2030-report-copy.pdf"
DEFAULT_DATASET = ROOT / ".data" / "datasets" / "enterprise-2030-needle-eval.json"
DEFAULT_EXTRACT = ROOT / "output" / "pdf-needle-eval" / "ground-truth-extract.txt"

# Speaker name -> (title fragment, quote index in smart-quote list or explicit quote)
KNOWN_SPEAKERS: list[dict[str, str]] = [
    {
        "id": "akiyuki_ui_quote",
        "speaker": "Akiyuki Ui",
        "title": "Operating Officer, Mizuho Bank",
        "quote_prefix": "The concept of",
    },
    {
        "id": "chad_gates_quote",
        "speaker": "Chad Gates",
        "title": "Managing Director, Pronto Software",
        "quote_prefix": "By 2030, insight will be everywhere",
    },
    {
        "id": "alex_schultz_quote",
        "speaker": "Alex Schultz",
        "title": "VP Analytics and CMO, Meta",
        "quote_prefix": "My marketing teams sit with engineering",
    },
    {
        "id": "tina_edmundson_quote",
        "speaker": "Tina Edmundson",
        "title": "President, Luxury, Marriott International",
        "quote_prefix": "In a world that is increasingly digital",
    },
    {
        "id": "aaron_levie_quote",
        "speaker": "Aaron Levie",
        "title": "CEO and Co-founder, Box",
        "quote_prefix": "AI neutralizes the classic advantage",
    },
]


def _extract_smart_quotes(text: str) -> list[str]:
    return re.findall(r"[\u201c\"]([^\u201d\"]{20,500})[\u201d\"]", text)


def _find_quote_by_prefix(quotes: list[str], prefix: str) -> str:
    prefix_lower = prefix.lower()
    for quote in quotes:
        if quote.lower().startswith(prefix_lower) or prefix_lower in quote.lower():
            return quote.strip()
    return ""


def _find_speaker_attribution(text: str, speaker: str) -> str:
    pattern = re.compile(
        rf"{re.escape(speaker)}\s*\n\s*([^\n]{{5,80}})",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _build_needle_item(
    *,
    item_id: str,
    speaker: str,
    title: str,
    quote: str,
    page_hint: str = "foreword / executive sections",
) -> dict[str, Any]:
    words = quote.split()
    mid = max(1, len(words) // 2)
    sub1 = " ".join(words[: min(8, len(words))])
    sub2 = " ".join(words[mid : mid + 8]) if len(words) > 8 else sub1
    return {
        "id": item_id,
        "category": "needle_quote",
        "query": (
            f"What is the exact quote from {speaker}, {title}? "
            "Return the quote verbatim and attribute it to the speaker."
        ),
        "speaker": speaker,
        "expected_substrings": [sub1, sub2],
        "expected_exact_quote": quote,
        "forbidden_substrings": [],
        "negative_control": False,
        "page_hint": page_hint,
    }


def build_gold_items(pdf_path: Path) -> tuple[list[dict[str, Any]], str]:
    text, metadata = read_document_content(pdf_path)
    quotes = _extract_smart_quotes(text)
    items: list[dict[str, Any]] = []

    for spec in KNOWN_SPEAKERS:
        quote = _find_quote_by_prefix(quotes, spec["quote_prefix"])
        if not quote:
            raise ValueError(f"Could not locate quote for {spec['speaker']} (prefix={spec['quote_prefix']!r})")
        title = spec["title"] or _find_speaker_attribution(text, spec["speaker"])
        items.append(
            _build_needle_item(
                item_id=spec["id"],
                speaker=spec["speaker"],
                title=title,
                quote=quote,
            )
        )

    # Numeric / factual needle
    if "131072" not in text and "100,474" not in text:
        year_match = re.search(r"\b20[2-3][0-9]\b", text[5000:15000])
        fact = year_match.group(0) if year_match else "2030"
    else:
        fact = "2030"
    items.append(
        {
            "id": "report_title_year",
            "category": "needle_fact",
            "query": "What year does the report title refer to for 'The enterprise in ...'?",
            "speaker": "",
            "expected_substrings": [fact, "enterprise"],
            "expected_exact_quote": "",
            "forbidden_substrings": [],
            "negative_control": False,
            "page_hint": "title page",
        }
    )

    # Section title needle
    items.append(
        {
            "id": "prediction_5_heading",
            "category": "needle_section",
            "query": "What is the exact title of Prediction 5 in the table of contents?",
            "speaker": "",
            "expected_substrings": ["Prediction 5", "Quantum"],
            "expected_exact_quote": "Prediction 5: Quantum will cause the next seismic shift.",
            "forbidden_substrings": [],
            "negative_control": False,
            "page_hint": "table of contents",
        }
    )

    # Negative controls
    items.append(
        {
            "id": "antarctica_quantum",
            "category": "negative_control",
            "query": (
                "What does Chad Gates say about Antarctica's quantum computing policy in this report? "
                "Quote verbatim if present."
            ),
            "speaker": "Chad Gates",
            "expected_substrings": [],
            "expected_exact_quote": "",
            "forbidden_substrings": ["antarctica", "quantum computing policy"],
            "negative_control": True,
            "page_hint": "not in document",
        }
    )
    items.append(
        {
            "id": "fictional_speaker",
            "category": "negative_control",
            "query": (
                "What is the exact quote from Dr. Evelyn Hawthorne, Chief AI Officer at Zephyr Dynamics? "
                "Return verbatim."
            ),
            "speaker": "Dr. Evelyn Hawthorne",
            "expected_substrings": [],
            "expected_exact_quote": "",
            "forbidden_substrings": ["evelyn hawthorne", "zephyr dynamics"],
            "negative_control": True,
            "page_hint": "not in document",
        }
    )

    # Optional synthesis (tracked separately)
    items.append(
        {
            "id": "foreword_ai_first_theme",
            "category": "synthesis",
            "query": (
                "In one sentence, what distinction does the foreword draw between "
                "'AI-enabled' and 'AI-first' enterprises?"
            ),
            "speaker": "",
            "expected_substrings": ["AI-enabled", "AI-first"],
            "expected_exact_quote": "",
            "forbidden_substrings": [],
            "negative_control": False,
            "page_hint": "foreword",
        }
    )

    header = (
        f"# Enterprise 2030 ground truth extract\n"
        f"# source: {pdf_path}\n"
        f"# chars: {len(text)}\n"
        f"# extraction: {metadata.get('extraction_method', 'unknown')}\n\n"
    )
    return items, header + text


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Enterprise 2030 needle eval gold set")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--extract-out", type=Path, default=DEFAULT_EXTRACT)
    args = parser.parse_args()

    if not args.pdf.is_file():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 1

    items, extract_text = build_gold_items(args.pdf)
    args.dataset.parent.mkdir(parents=True, exist_ok=True)
    args.extract_out.parent.mkdir(parents=True, exist_ok=True)

    args.dataset.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.extract_out.write_text(extract_text, encoding="utf-8")

    print(f"Wrote {len(items)} gold items to {args.dataset}")
    print(f"Wrote extract ({len(extract_text)} chars) to {args.extract_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
