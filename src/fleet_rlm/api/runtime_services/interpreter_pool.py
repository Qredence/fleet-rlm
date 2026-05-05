"""Centralized Daytona interpreter acquire/release lifecycle manager."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import ServerRuntimeConfig

logger = logging.getLogger(__name__)


class InterpreterPool:
    """Owns Daytona interpreter acquire/release lifecycle.

    The pool centralizes interpreter construction and safe shutdown.
    It handles Daytona configuration errors and cleanup failures gracefully.
    """

    async def acquire(self, cfg: ServerRuntimeConfig) -> Any | None:
        """Build and return a Daytona interpreter.

        Returns ``None`` if Daytona is not configured or available.
        Construction errors are caught and logged.
        """
        try:
            from fleet_rlm.integrations.daytona.config import DaytonaConfigError
            from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

            interpreter = DaytonaInterpreter(
                volume_name=cfg.volume_name,
                timeout=cfg.timeout,
                max_llm_calls=cfg.rlm_max_llm_calls,
                max_recursion_depth=cfg.rlm_max_depth,
                rlm_max_iterations=cfg.rlm_max_iterations,
                child_isolation_mode=cfg.rlm_child_isolation_mode,
                child_fork_fallback=cfg.rlm_child_fork_fallback,
                delegate_max_calls_per_turn=cfg.delegate_max_calls_per_turn,
                delegate_result_truncation_chars=cfg.delegate_result_truncation_chars,
                async_execute=cfg.interpreter_async_execute,
            )
            interpreter._host_repository = None
            interpreter._host_identity = None
            interpreter._host_run_id = None
            return interpreter
        except ImportError:
            return None
        except DaytonaConfigError:
            return None
        except Exception as exc:
            logger.warning("InterpreterPool.acquire failed: %s", exc)
            return None

    async def release(self, interpreter: Any | None) -> None:
        """Safely shutdown a Daytona interpreter.

        Swallows all exceptions so that cleanup never raises.
        """
        if interpreter is None:
            return

        try:
            ashutdown = getattr(interpreter, "ashutdown", None)
            if callable(ashutdown):
                await ashutdown()
                return
            shutdown = getattr(interpreter, "shutdown", None)
            if callable(shutdown):
                await asyncio.to_thread(shutdown)
        except Exception as exc:
            logger.warning("InterpreterPool.release failed: %s", exc)


__all__ = ["InterpreterPool"]
