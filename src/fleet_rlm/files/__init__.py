"""Attachment upload and staging.

Import concrete catalog, lifecycle, and tool types from their owning modules.
Keeping package initialization side-effect free avoids loading host-tool graphs
on submodule imports.
"""

from __future__ import annotations
