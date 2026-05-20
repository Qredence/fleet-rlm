"""Import-hook installer for patched DSPy modules.

DSPy 3.2.1 eagerly imports ``dspy.teleprompt.avatar_optimizer`` from the
package root, and that upstream module still constructs signatures with the
deprecated ``prefix=`` field argument. We redirect just that module to a local
drop-in implementation that preserves the public ``AvatarOptimizer`` surface
without deprecated field usage.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from pathlib import Path
from typing import Final

_PATCHED_MODULES: Final[dict[str, Path]] = {
    "dspy.teleprompt.avatar_optimizer": Path(__file__).with_name("avatar_optimizer.py"),
}


class _PatchedDspyModuleFinder(importlib.abc.MetaPathFinder):
    """Redirect selected DSPy imports to local patched source files."""

    def __init__(self, module_paths: dict[str, Path]) -> None:
        self._module_paths = {name: path.resolve() for name, path in module_paths.items()}

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        _ = path, target
        source_path = self._module_paths.get(fullname)
        if source_path is None or not source_path.is_file():
            return None
        return importlib.util.spec_from_file_location(fullname, source_path)


_FINDER = _PatchedDspyModuleFinder(_PATCHED_MODULES)


def install() -> None:
    """Install the patched DSPy import finder exactly once."""
    if _FINDER in sys.meta_path:
        return
    sys.meta_path.insert(0, _FINDER)


__all__ = ["install"]
