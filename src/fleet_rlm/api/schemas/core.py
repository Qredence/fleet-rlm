"""Backward-compatible schema re-exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import *  # noqa: F403
    from .feedback import *  # noqa: F403
    from .memory import *  # noqa: F403
    from .optimization import *  # noqa: F403
    from .runtime import *  # noqa: F403
    from .sandbox import *  # noqa: F403
    from .sessions import *  # noqa: F403
    from .volumes import *  # noqa: F403
    from .websocket import *  # noqa: F403

# Runtime lazy re-exports to preserve import compatibility
from .base import *  # noqa: F403
from .feedback import *  # noqa: F403
from .memory import *  # noqa: F403
from .optimization import *  # noqa: F403
from .runtime import *  # noqa: F403
from .sandbox import *  # noqa: F403
from .sessions import *  # noqa: F403
from .volumes import *  # noqa: F403
from .websocket import *  # noqa: F403
