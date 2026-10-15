"""Route-boundary error translation: domain errors become HTTP errors without leaking internals."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

_LOGGER = logging.getLogger("fleet_rlm.api")


@contextmanager
def translate(unavailable: str, *, invalid: str | None = None, status: int = 503) -> Iterator[None]:
    """ValueError → 422 (`invalid`, or the error text); any other failure → `status` with `unavailable`.

    HTTPExceptions raised inside pass through unchanged. Unexpected failures are logged by type only.
    """
    try:
        yield
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=invalid or str(exc)) from None
    except Exception as exc:
        _LOGGER.warning("%s (%s)", unavailable, type(exc).__name__)
        raise HTTPException(status_code=status, detail=unavailable) from None
