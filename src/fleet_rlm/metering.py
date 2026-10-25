"""The metered host-LM proxy: one budget charge per completion, credentials left on the wrapped LM."""

from __future__ import annotations

from typing import Any

# Pinned to DSPy 3.4.x. `SandboxLM` is not exported from a public namespace, and it owns the
# admission check that keeps sandboxed code to `SANDBOX_LM_KWARGS`; do not reimplement either.
from dspy.primitives.facade import SandboxLM


class MeteredLM(SandboxLM):
    """Charge the shared Turn budget before forwarding each call to the wrapped LM."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Charge the call, then forward it to the wrapped LM."""
        self._admit(kwargs)
        return self._lm(*args, **kwargs)

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        """Async variant of __call__ with the same charge."""
        self._admit(kwargs)
        return await self._lm.acall(*args, **kwargs)

    def copy(self, **kwargs: Any) -> MeteredLM:
        """Return a metered copy that keeps the subclass and its reserve callback."""
        self._check(kwargs)
        return type(self)(self._lm.copy(**kwargs), self._reserve)
