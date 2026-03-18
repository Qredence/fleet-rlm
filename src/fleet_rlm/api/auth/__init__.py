"""Server auth abstraction with dev and Entra providers."""

from .admission import resolve_admitted_identity
from .base import AuthError, AuthProvider
from .dev import DevAuthProvider
from .entra import EntraAuthProvider
from .factory import build_auth_provider
from .types import NormalizedIdentity

__all__ = [
    "AuthError",
    "AuthProvider",
    "DevAuthProvider",
    "EntraAuthProvider",
    "NormalizedIdentity",
    "build_auth_provider",
    "resolve_admitted_identity",
]
