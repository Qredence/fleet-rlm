"""Closed Session domain for durable conversation state.

Import concrete catalog, history, and model types from their owning modules.
Keeping package initialization side-effect free avoids loading persistence
graphs on submodule imports.
"""

from __future__ import annotations
