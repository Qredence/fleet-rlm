"""Context managers to restore sys.modules after import/reload test pollution.

Used by tests that deliberately reload modules or delete sys.modules entries to
verify import-time behavior. Without restoration, later tests in the same
pytest process can observe stale class identities (e.g. ChatExecutionContext).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def restore_sys_modules(*module_names: str) -> Iterator[None]:
    """Preserve and restore exact ``sys.modules`` entries for *module_names*."""
    saved = {name: sys.modules.get(name) for name in module_names}
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)


@contextmanager
def restore_sys_modules_matching(*substrings: str) -> Iterator[None]:
    """Preserve and restore all ``sys.modules`` keys containing any substring."""
    saved = {k: v for k, v in sys.modules.items() if any(s in k for s in substrings)}
    try:
        yield
    finally:
        current = [k for k in sys.modules if any(s in k for s in substrings)]
        for key in current:
            if key not in saved:
                del sys.modules[key]
        sys.modules.update(saved)


@contextmanager
def isolated_module_reload(module_name: str) -> Iterator[object]:
    """``importlib.reload`` inside the block; restore pre-test ``sys.modules`` entry after."""
    pre = sys.modules.get(module_name)
    try:
        mod = importlib.import_module(module_name)
        yield importlib.reload(mod)
    finally:
        if pre is not None:
            sys.modules[module_name] = pre
        else:
            sys.modules.pop(module_name, None)
            importlib.import_module(module_name)
