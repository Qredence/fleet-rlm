"""Shared default values for RLM long-context pipeline scripts."""

from __future__ import annotations

DEFAULT_CHUNK_SIZE: int = 200_000
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.95
DEFAULT_STATE_PATH: str = ".claude/rlm_state/state.pkl"
DEFAULT_CHUNKS_DIR: str = ".claude/rlm_state/chunks"
DEFAULT_CACHE_DIR: str = ".claude/rlm_state/cache"
