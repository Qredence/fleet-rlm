"""Document chunking strategies for long-context RLM workflows.

This module provides pure functions for splitting large documents into
manageable chunks. These functions are designed to be:

    1. Self-contained (stdlib-only) so they can be injected into the sandbox
    2. Importable host-side for tests and notebooks
    3. Usable by the LLM inside the dspy.RLM REPL loop

Chunking strategies:
    - chunk_by_size: Fixed-size chunking with optional overlap
    - chunk_by_headers: Split markdown/structured text by header boundaries
    - chunk_by_timestamps: Split log files by timestamp patterns
    - chunk_by_json_keys: Split JSON objects into per-key chunks

All functions use only the Python standard library (re, json) so they
can be serialized into the Modal sandbox environment.
"""

from __future__ import annotations

from .size import chunk_by_size
from .headers import chunk_by_headers
from .timestamps import chunk_by_timestamps
from .json_keys import chunk_by_json_keys

__all__ = [
    "chunk_by_size",
    "chunk_by_headers",
    "chunk_by_timestamps",
    "chunk_by_json_keys",
]
