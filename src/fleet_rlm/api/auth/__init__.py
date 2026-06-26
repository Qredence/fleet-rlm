"""Server auth abstraction using Neon Auth."""

from .admission import resolve_admitted_identity
from .base import AuthError, AuthProvider
from .factory import build_auth_provider
from .neon import NeonAuthProvider
from .types import NormalizedIdentity
from .ws_ticket import WebSocketTicketStore

__all__ = [
    "AuthError",
    "AuthProvider",
    "NeonAuthProvider",
    "NormalizedIdentity",
    "WebSocketTicketStore",
    "build_auth_provider",
    "resolve_admitted_identity",
]
