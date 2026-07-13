"""Artifact validation: kind, title, size, content shape."""

from __future__ import annotations

import json
import re
from typing import cast, get_args

from fleet_rlm.artifacts.errors import ArtifactValidationError
from fleet_rlm.artifacts.models import (
    KIND_MEDIA_TYPES,
    ArtifactKind,
)

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_SAFE_TITLE = re.compile(r"^[A-Za-z0-9._ -]{1,255}$")
_ALLOWED_KINDS = frozenset(get_args(ArtifactKind))


def parse_kind(kind: str) -> ArtifactKind:
    raw = (kind or "").strip().lower()
    if raw not in _ALLOWED_KINDS:
        raise ArtifactValidationError(f"unsupported artifact kind; expected one of {sorted(_ALLOWED_KINDS)}")
    return cast(ArtifactKind, raw)


def media_type_for(kind: ArtifactKind) -> str:
    return KIND_MEDIA_TYPES[kind]


def sanitize_title(title: str | None) -> str | None:
    if title is None:
        return None
    raw = title.strip()
    if not raw:
        return None
    if "/" in raw or "\\" in raw or ".." in raw:
        raise ArtifactValidationError("invalid title")
    if not _SAFE_TITLE.match(raw):
        raise ArtifactValidationError("invalid title")
    return raw


def validate_content_size(size: int, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    if size < 0:
        raise ArtifactValidationError("negative size")
    if size == 0:
        raise ArtifactValidationError("empty content")
    if size > max_bytes:
        raise ArtifactValidationError(f"artifact exceeds max size of {max_bytes} bytes")


def encode_content(kind: ArtifactKind, content: str) -> bytes:
    """Validate textual content for kind and return UTF-8 bytes."""
    if not isinstance(content, str):
        raise ArtifactValidationError("content must be a string")
    text = content
    if kind == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError("content is not valid JSON") from exc
    data = text.encode("utf-8")
    return data
