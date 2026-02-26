"""WebSocket router package.

Canonical WS implementation lives under this package; legacy flat
``ws_*`` modules remain as one-release compatibility shims.
"""

from .api import chat_streaming, execution_stream, router

__all__ = ["router", "chat_streaming", "execution_stream"]
