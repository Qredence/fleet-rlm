"""Owned post-commit Workspace Memory promotion work."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

PostCommitPromotionStatus = Literal["completed", "deadline_exceeded", "interrupted", "failed"]


@dataclass(frozen=True, slots=True)
class PostCommitPromotionAttempt:
    """One bounded wait outcome for an owned promotion effect."""

    status: PostCommitPromotionStatus
    result: Any = None


class OwnedPostCommitMemoryPromotion:
    """Keep one synchronous promotion effect owned until its dependency lease closes."""

    def __init__(self, action: Callable[[tuple[Any, ...]], Any]) -> None:
        self._action = action
        self._task: asyncio.Task[Any] | None = None

    def __call__(self, candidates: tuple[Any, ...]) -> Any:
        """Retain compatibility with simple lifecycle adapters and tests."""
        return self._action(candidates)

    async def promote(
        self,
        candidates: tuple[Any, ...],
        *,
        timeout_s: float,
    ) -> PostCommitPromotionAttempt:
        """Start the effect once and wait only through the post-commit deadline."""
        if self._task is not None:
            raise RuntimeError("post-commit Memory promotion already started")
        self._task = asyncio.create_task(
            asyncio.to_thread(self._action, candidates),
            name="fleet-post-commit-memory-promotion",
        )
        try:
            result = await asyncio.wait_for(asyncio.shield(self._task), timeout=max(0.0, timeout_s))
        except TimeoutError:
            return PostCommitPromotionAttempt("deadline_exceeded")
        except asyncio.CancelledError:
            return PostCommitPromotionAttempt("interrupted")
        except BaseException:
            return PostCommitPromotionAttempt("failed")
        return PostCommitPromotionAttempt("completed", result)

    async def wait_owned(self) -> None:
        """Settle started work before the prepared resources it uses are released."""
        task = self._task
        if task is None:
            return
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if task.done() and not task.cancelled():
            with contextlib.suppress(BaseException):
                task.exception()


__all__ = [
    "OwnedPostCommitMemoryPromotion",
    "PostCommitPromotionAttempt",
    "PostCommitPromotionStatus",
]
