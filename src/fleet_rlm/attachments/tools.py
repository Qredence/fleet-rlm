"""Host-mediated Attachment tools for :class:`dspy.RLM`."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

import dspy

from fleet_rlm.attachments.models import AttachmentRef, StagedAttachment
from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import ToolEventView, bound_event_text
from fleet_rlm.workspace.storage import VolumeBlobFs

_EVENT_TEXT_MAX_CHARS = 256


def _project_fields(result: object, fields: tuple[str, ...]) -> JsonValue:
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        key: bound_event_text(values[key], max_chars=_EVENT_TEXT_MAX_CHARS)
        if isinstance(values[key], str)
        else values[key]
        for key in fields
        if key in values
    }


class AttachmentToolHost:
    """Bound Attachment reads for one Run; reauthorize every call."""

    def __init__(
        self,
        *,
        attachments: tuple[AttachmentRef, ...],
        staged_attachments: tuple[StagedAttachment, ...],
        volume_fs: VolumeBlobFs,
    ) -> None:
        self._attachments = {ref.id: ref for ref in attachments}
        self._staged = {item.attachment_id: item for item in staged_attachments}
        self._volume_fs = volume_fs
        self._pending_events: list[dict[str, Any]] = []

    def drain_public_events(self) -> list[dict[str, Any]]:
        """Return and clear ledger entries with bounded metadata only."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        """Record sandbox-local verified reads without reading the body again."""
        seen: set[UUID] = set()
        for value in attachment_ids:
            try:
                attachment_id = UUID(str(value))
            except (TypeError, ValueError, AttributeError):
                continue
            if attachment_id in seen:
                continue
            ref = self._attachments.get(attachment_id)
            if ref is None or attachment_id not in self._staged:
                continue
            seen.add(attachment_id)
            self._pending_events.append(
                {
                    "event_kind": "attachment.read",
                    "attachment_id": str(ref.id),
                    "filename": ref.filename,
                    "byte_size": ref.byte_size,
                }
            )

    async def aclose(self) -> None:
        """Attachment reads do not own private staging bytes."""
        return None

    def read_attachment(self, attachment_id: str) -> dict[str, Any]:
        """Return attachment body after reauth. Public event omits content."""
        try:
            aid = UUID(str(attachment_id).strip())
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "error": "invalid_id"}
        ref = self._attachments.get(aid)
        staged = self._staged.get(aid)
        if ref is None or staged is None:
            return {"ok": False, "error": "not_found"}
        try:
            data = self._volume_fs.read_bytes(staged.sandbox_path, use_cache=False)
            if (
                not isinstance(ref.checksum_sha256, str)
                or len(data) != ref.byte_size
                or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), ref.checksum_sha256.lower())
            ):
                return {"ok": False, "error": "not_found"}
        except Exception:
            return {"ok": False, "error": "not_found"}

        self._pending_events.append(
            {
                "event_kind": "attachment.read",
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "byte_size": ref.byte_size,
            }
        )

        # Prefer UTF-8 text; binary as base64.
        try:
            text = data.decode("utf-8")
            if "\x00" in text:
                raise UnicodeDecodeError("utf-8", data, 0, 1, "nul")
            return {
                "ok": True,
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "content_type": ref.content_type,
                "content": text,
                "encoding": "utf-8",
            }
        except UnicodeDecodeError:
            return {
                "ok": True,
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "content_type": ref.content_type,
                "content_base64": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            }

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Return the canonical Attachment tool."""

        def read_attachment(attachment_id: str) -> dict[str, Any]:
            """Read an authorized attachment by opaque ID (host rechecks every call)."""
            return self.read_attachment(attachment_id)

        return (
            dspy.Tool(
                read_attachment,
                name="read_attachment",
                desc=(
                    "Read one immutable authorized Attachment by opaque identity only when its advertised "
                    "metadata is relevant to the current request."
                ),
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return the bounded public projection for the Attachment tool."""

        def read_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {"attachment_id": bound_event_text(arguments.get("attachment_id"))}

        return MappingProxyType(
            {
                "read_attachment": ToolEventView(
                    input_projection=read_input,
                    output_projection=lambda result: _project_fields(
                        result,
                        ("ok", "error", "attachment_id", "filename", "content_type", "encoding"),
                    ),
                )
            }
        )


__all__ = ["AttachmentToolHost"]
