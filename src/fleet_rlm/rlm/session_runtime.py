"""Provider-neutral resident Root RLM state for one Fleet Session.

This module is an intentionally small seam between the native ``dspy.RLM``
and a provider-specific interpreter/Root Sandbox adapter.  It owns only
compatibility, admission, serial execution and resident-state cleanup.  It
does not build an RLM, bind current-turn Tools, reset interpreter globals, or
know about Daytona (or any other provider).

The registry is suitable for the later Session runtime integration.  A caller
supplies a factory that creates a :class:`SessionRLMState` and may inject a
cleanup callback for provider resources.  The same state is returned for a
compatible Session until it is tainted, rotated, evicted, deleted, or the
registry shuts down.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import inspect
import json
import logging
import math
import re
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import dspy

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_SESSION_TOOL_COUNT",
    "MAX_TOOL_ARGUMENTS",
    "MAX_TOOL_DESCRIPTION_CHARS",
    "MAX_TOOL_NAME_CHARS",
    "MAX_TOOL_SCHEMA_BYTES",
    "AuthorizationCheck",
    "ClaimCheck",
    "CleanupCallback",
    "ProgramFingerprint",
    "ProgramFingerprintComponents",
    "RuntimeFactory",
    "RuntimeHealth",
    "RuntimeUnavailableError",
    "SessionKey",
    "SessionRLMRegistry",
    "SessionRLMState",
    "SessionRuntimeLease",
    "SessionRuntimeRegistry",
    "SessionToolAuthorizationError",
    "SessionToolBinding",
    "SessionToolRegistry",
    "SessionToolUnavailableError",
    "ToolAuthorization",
    "compute_program_fingerprint",
    "digest_program_components",
    "fingerprint_components",
    "program_fingerprint",
]


# ---------------------------------------------------------------------------
# Program compatibility fingerprints
# ---------------------------------------------------------------------------


# These values are turn/session data, not program compatibility.  They are
# dropped at the component boundary before canonical JSON is built.  The
# structural ``signature_fields``/``tools``/``output_contract`` containers are
# handled separately so a field named ``request`` does not get mistaken for a
# request body.
_EXCLUDED_COMPONENT_KEYS = frozenset(
    {
        "request",
        "request_text",
        "user_request",
        "prompt",
        "query",
        "history",
        "history_body",
        "history_contents",
        "transcript",
        "conversation",
        "attachments",
        "attachment",
        "attachment_id",
        "attachment_ids",
        "memory",
        "memory_contents",
        "memory_digest",
        "memories",
        "secrets",
        "secret",
        "secret_value",
        "credentials",
        "credential",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "password",
        "passwd",
        "authorization",
        "bearer_token",
        "private_key",
        "client_secret",
    }
)

# Secret-bearing configuration keys are omitted even when a caller passes an
# open-ended LM/tool mapping.  Compatibility configuration remains explicit
# and can therefore include non-secret endpoint/model choices.
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:secret|token|password|credential|authorization|bearer|"
    r"api[ _-]?key|access[ _-]?key|private[ _-]?key)\s*[:=]\s*[^,;\s]+"
)


_SECRET_KEY_PARTS = frozenset(
    {
        "secret",
        "token",
        "password",
        "passwd",
        "credential",
        "authorization",
        "bearer",
        "api_key",
        "apikey",
        "private_key",
        "client_secret",
    }
)

# A schema container carries compatibility shape.  Its field names must be
# retained (including e.g. a Signature field called ``request``); scalar
# values whose *keys* are secret-bearing are still omitted below.
_STRUCTURAL_KEYS = frozenset(
    {
        "signature_fields",
        "signature_schema",
        "input_fields",
        "output_fields",
        "tools",
        "tool_names",
        "tool_descriptions",
        "tool_schemas",
        "schema",
        "properties",
        "required",
        "items",
        "output_contract",
        "output_schema",
        "skill_signature",
        "skill",
    }
)


def _key_text(key: object) -> str:
    """Return a stable textual mapping key without calling arbitrary repr."""
    if isinstance(key, str):
        return key
    if isinstance(key, (int, float, bool)) or key is None:
        return str(key)
    return f"<{type(key).__module__}.{type(key).__qualname__}>"


def _normalized_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _public_endpoint(value: object) -> object:
    """Retain endpoint routing while dropping query/fragment credentials."""
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]
    if not parsed.scheme and not parsed.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    try:
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host += f":{parsed.port}"
    except ValueError:
        host = parsed.hostname or ""
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _is_excluded_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized in _EXCLUDED_COMPONENT_KEYS:
        return True
    if normalized.endswith(("_request", "_history", "_transcript", "_conversation")):
        return True
    return normalized.startswith(("attachment_", "memory_", "history_"))


def _is_secret_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized in _EXCLUDED_COMPONENT_KEYS:
        return True
    if normalized in {"tokens", "token_count", "input_tokens", "output_tokens", "total_tokens"}:
        return False
    if normalized.endswith("_tokens"):
        return False
    if normalized in _SECRET_KEY_PARTS:
        return True
    return any(normalized.startswith(f"{part}_") or normalized.endswith(f"_{part}") for part in _SECRET_KEY_PARTS)


def _qualified_name(value: object) -> str:
    cls = value if isinstance(value, type) else type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _signature_class_components(value: type[Any]) -> dict[str, Any] | None:
    """Extract public Signature shape when a class is supplied as a component."""
    try:
        if not issubclass(value, dspy.Signature):
            return None
    except TypeError:
        return None

    result: dict[str, Any] = {"class": _qualified_name(value)}
    instructions = getattr(value, "instructions", None)
    if isinstance(instructions, str):
        result["instructions"] = instructions
    for name in ("input_fields", "output_fields"):
        field_map = getattr(value, name, None)
        if isinstance(field_map, Mapping):
            result[name] = field_map
    return result


def _annotation_shape(value: object) -> str:
    """Return a deterministic annotation label without retaining values."""
    if isinstance(value, type):
        return _qualified_name(value)
    module = getattr(value, "__module__", "")
    if isinstance(module, str) and module in {"typing", "types", "builtins"}:
        return repr(value)[:512]
    return _qualified_name(value)


def _object_components(value: object) -> dict[str, Any] | None:
    """Extract a bounded public shape from common config objects."""
    if isinstance(value, dspy.Tool):
        return {
            "class": _qualified_name(value),
            "name": value.name,
            "description": value.desc or "",
            "args": dict(value.args) if isinstance(value.args, Mapping) else {},
            "arg_types": {
                str(name): _annotation_shape(annotation) for name, annotation in (value.arg_types or {}).items()
            }
            if isinstance(value.arg_types, Mapping)
            else {},
            "arg_descriptions": dict(value.arg_desc) if isinstance(value.arg_desc, Mapping) else {},
        }

    # DSPy Signature fields are Pydantic FieldInfo values.  Their class alone
    # is not a compatibility contract: annotation, requiredness and public
    # description must participate in the digest.
    required_check = getattr(value, "is_required", None)
    if hasattr(value, "annotation") and callable(required_check):
        try:
            required = bool(required_check())
        except Exception:
            required = True
        extra = getattr(value, "json_schema_extra", None)
        description = getattr(value, "description", None) or ""
        if isinstance(extra, Mapping) and isinstance(extra.get("desc"), str):
            description = extra["desc"]
        shape: dict[str, Any] = {
            "class": _qualified_name(value),
            "annotation": _annotation_shape(getattr(value, "annotation", str)),
            "required": required,
            "description": description,
            "has_default_factory": getattr(value, "default_factory", None) is not None,
        }
        if not required and getattr(value, "default_factory", None) is None:
            default = getattr(value, "default", None)
            if isinstance(default, (str, bool, int, float)) or default is None:
                shape["default"] = default
            else:
                shape["default_type"] = _qualified_name(default)
        return shape

    if isinstance(value, type):
        signature_shape = _signature_class_components(value)
        if signature_shape is not None:
            return signature_shape
        return {"class": _qualified_name(value)}

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return dict(dumped)

    # DSPy LMs and lightweight test config objects expose these public shape
    # attributes.  Never walk ``__dict__``: it commonly contains credentials,
    # provider clients and request history.
    public_attrs = (
        "model",
        "provider",
        "base_url",
        "api_base",
        "endpoint",
        "temperature",
        "max_tokens",
        "timeout",
        "num_retries",
        "cache",
        "model_type",
    )
    attrs: dict[str, Any] = {}
    for name in public_attrs:
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if callable(item):
            continue
        attrs[name] = item
    if attrs:
        attrs["class"] = _qualified_name(value)
        return attrs
    if callable(value):
        return {"callable": _qualified_name(value)}
    return {"class": _qualified_name(value)}


_FINGERPRINT_MAX_DEPTH = 32
_FINGERPRINT_MAX_NODES = 4096
_FINGERPRINT_MAX_ITEMS = 1024
_FINGERPRINT_MAX_TEXT_CHARS = 65_536

# Compatibility fingerprints are built from a small, typed vocabulary.  Open
# mappings are still accepted for migration callers, but values outside these
# names are represented only by type/size shape.  In particular, a generic
# mapping must not become an accidental channel for provider credentials.
_FINGERPRINT_TEXT_KEYS = frozenset(
    {
        "description",
        "descriptions",
        "instructions",
        "signature_instructions",
        "skill_instructions",
        "title",
        "pattern",
        "default",
        "const",
        "stop",
        "$ref",
    }
)
_FINGERPRINT_PUBLIC_LM_KEYS = frozenset(
    {
        "class",
        "model",
        "model_type",
        "provider",
        "base_url",
        "api_base",
        "endpoint",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "stop",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "num_retries",
        "timeout",
        "cache",
        "reasoning_effort",
        "response_format",
        "use_developer_role",
        "finetuning_model",
        "custom_llm_provider",
        "deployment_id",
        "parallel_tool_calls",
    }
)
_FINGERPRINT_PUBLIC_POLICY_KEYS = frozenset(
    {
        "enabled",
        "root_depth",
        "child_depth",
        "max_calls",
        "max_children",
        "max_prompt_chars",
        "child_max_iters",
        "child_max_llm_calls",
        "child_max_output_chars",
        "max_parallel_children",
        "max_iters",
        "max_llm_calls",
        "max_output_chars",
    }
)
_FINGERPRINT_PUBLIC_FIELD_KEYS = frozenset(
    {
        "annotation",
        "type",
        "required",
        "has_default_factory",
        "class",
        "name",
    }
)
_FINGERPRINT_PUBLIC_SCHEMA_KEYS = frozenset(
    {
        "type",
        "required",
        "nullable",
        "additionalproperties",
        "format",
        "exclusivemaximum",
        "exclusiveminimum",
        "maximum",
        "maxitems",
        "maxlength",
        "minimum",
        "minitems",
        "minlength",
        "uniqueitems",
    }
)


def _shape_only(value: object) -> object:
    """Return only bounded type/size information for an open-ended value."""
    if isinstance(value, str):
        if len(value) > _FINGERPRINT_MAX_TEXT_CHARS:
            raise ValueError("program fingerprint text component exceeds bound")
        return {"__text_length__": len(value)}
    if value is None:
        return {"__type__": "null"}
    if isinstance(value, bool):
        return {"__type__": "bool"}
    if isinstance(value, int):
        return {"__type__": "int"}
    if isinstance(value, float):
        return {"__type__": "float"}
    if isinstance(value, bytes):
        if len(value) > _FINGERPRINT_MAX_TEXT_CHARS:
            raise ValueError("program fingerprint byte component exceeds bound")
        return {"__bytes_length__": len(value)}
    if isinstance(value, Mapping):
        if len(value) > _FINGERPRINT_MAX_ITEMS:
            raise ValueError("program fingerprint mapping exceeds item bound")
        # Do not retain arbitrary mapping keys: a credential can itself be a
        # key, and open mappings are not part of the compatibility contract.
        return {"__mapping_length__": len(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        if len(value) > _FINGERPRINT_MAX_ITEMS:
            raise ValueError("program fingerprint sequence exceeds item bound")
        return {"__sequence_type__": _qualified_name(type(value)), "__sequence_length__": len(value)}
    return {"__type__": _qualified_name(value)}


def _text_shape(value: object) -> object:
    """Retain public text shape but never retain or hash text content."""
    if isinstance(value, str):
        if len(value) > _FINGERPRINT_MAX_TEXT_CHARS:
            raise ValueError("program fingerprint text component exceeds bound")
        if _SECRET_TEXT_RE.search(value):
            return {"__redacted_text__": True}
        return {"__text_length__": len(value)}
    # Runner-side projections already use these safe markers. Preserve them
    # when canonicalization is applied a second time; arbitrary mappings still
    # cross the generic shape-only boundary below.
    if isinstance(value, Mapping) and set(value) in ({"__text_length__"}, {"__redacted_text__"}):
        if set(value) == {"__redacted_text__"} and value.get("__redacted_text__") is True:
            return {"__redacted_text__": True}
        length = value.get("__text_length__")
        if isinstance(length, int) and 0 <= length <= _FINGERPRINT_MAX_TEXT_CHARS:
            return {"__text_length__": length}
    return _shape_only(value)


def _canonical_child_mode(mode: str, key: str, raw_value: object) -> str:
    """Select the allow-listed value policy for one canonical mapping child."""
    normalized = _normalized_key(key)
    if mode == "root":
        if normalized in {"root_lm_config", "root_lm"}:
            return "lm"
        if normalized in {"sub_lm_config", "sub_lm"}:
            return "lm"
        if normalized in {"signature_fields", "signature_schema", "input_fields", "output_fields"}:
            return "fields"
        if normalized in {"signature_instructions", "skill_instructions"}:
            return "text_sequence" if isinstance(raw_value, (list, tuple)) else "text"
        if normalized == "tools":
            return "tools"
        if normalized == "tool_names":
            return "public_sequence"
        if normalized == "tool_descriptions":
            return "text_sequence"
        if normalized == "tool_schemas":
            return "schema_sequence"
        if normalized in {"recursion_policy", "recursion", "limits"}:
            return "policy"
        if normalized in {"output_contract", "output_schema"}:
            return "contract"
        if normalized in {"skill_signature", "skill"}:
            return "skill"
        if normalized in {"dspy_version", "interpreter_protocol_version", "interpreter_version"}:
            return "public"
        # Mapping callers sometimes provide a flat model/limit component. Keep
        # the documented public scalar names compatible in that form too.
        if normalized in _FINGERPRINT_PUBLIC_LM_KEYS | _FINGERPRINT_PUBLIC_POLICY_KEYS:
            return "text" if normalized in _FINGERPRINT_TEXT_KEYS else "public"
        return "shape"
    if mode == "lm":
        if normalized not in _FINGERPRINT_PUBLIC_LM_KEYS:
            return "shape"
        if normalized in _FINGERPRINT_TEXT_KEYS:
            return "text" if normalized == "stop" else "text_sequence"
        if normalized == "response_format":
            return "shape"
        return "public_endpoint" if normalized in {"base_url", "api_base", "endpoint"} else "public"
    if mode == "policy":
        return "public" if normalized in _FINGERPRINT_PUBLIC_POLICY_KEYS else "shape"
    if mode == "fields":
        if normalized in {"inputs", "outputs"}:
            return "field_sequence"
        return "field"
    if mode == "signature_shape":
        if normalized in {"inputs", "outputs"}:
            return "field_sequence"
        if normalized == "instructions":
            return "text"
        return "shape"
    if mode == "field":
        if normalized in _FINGERPRINT_PUBLIC_FIELD_KEYS:
            return "public"
        if normalized in _FINGERPRINT_TEXT_KEYS:
            return "text"
        return "shape"
    if mode == "contract":
        if normalized in {"schema_id", "schema_version"}:
            return "public"
        if normalized == "fields":
            return "field_sequence"
        # A compact mapping such as {"answer": {"type": "string"}} is
        # treated as output-field metadata.
        return "field"
    if mode == "skill":
        if normalized == "cards":
            return "pair_sequence"
        if normalized in {"signature", "name", "version"}:
            return "public"
        if normalized in _FINGERPRINT_TEXT_KEYS:
            return "text"
        return "shape"
    if mode == "tools":
        if normalized in {"names", "tool_names"}:
            return "public_sequence"
        if normalized in {"descriptions", "tool_descriptions"}:
            return "text_sequence"
        if normalized in {"schemas", "tool_schemas"}:
            return "schema_sequence"
        return "tool"
    if mode == "tool":
        if normalized in {"class", "name"}:
            return "public"
        if normalized in {"description", "arg_descriptions"}:
            return "text" if normalized == "description" else "text_map"
        if normalized == "args":
            # DSPy Tool ``args`` maps arbitrary argument names to individual
            # JSON-schema objects; retain each schema's public type/limits.
            return "schema_fields"
        if normalized == "schema":
            return "schema"
        if normalized == "arg_types":
            return "public_map"
        return "shape"
    if mode == "schema_fields":
        # Property names are arbitrary but their values are JSON-schema
        # objects whose public type/constraints remain compatibility inputs.
        return "schema"
    if mode == "schema":
        if normalized in _FINGERPRINT_TEXT_KEYS:
            return "text"
        if normalized == "properties":
            return "schema_fields"
        if normalized in {"items", "anyof", "oneof", "allof", "not", "additionalproperties"}:
            return "schema"
        if normalized == "required":
            return "public_sequence"
        if normalized == "enum":
            return "shape"
        if normalized in _FINGERPRINT_PUBLIC_SCHEMA_KEYS:
            return "public"
        return "shape"
    if mode == "public":
        return "public" if normalized == "class" else "shape"
    if mode == "public_map":
        return "public"
    if mode == "text_map":
        return "text"
    if mode == "public_endpoint":
        return "public"
    return "shape"


def _canonical_value(
    value: object,
    *,
    structural: bool = False,
    _active: set[int] | None = None,
    _nodes: list[int] | None = None,
    _depth: int = 0,
    _mode: str = "shape",
) -> object:
    """Convert one component to deterministic, bounded, secret-free JSON data.

    ``_mode`` is deliberately private.  It lets the public compatibility
    fields retain their documented values while every open-ended or free-text
    value crosses a shape-only boundary.  This is stronger than key-only
    secret filtering: an unknown key cannot smuggle a credential into the
    canonical object merely by using a benign name.
    """
    if _active is None:
        _active = set()
    if _nodes is None:
        _nodes = [0]
    _nodes[0] += 1
    if _nodes[0] > _FINGERPRINT_MAX_NODES or _depth > _FINGERPRINT_MAX_DEPTH:
        raise ValueError("program fingerprint components exceed bounded shape")

    if _mode == "shape":
        return _shape_only(value)
    if _mode in {"text", "text_sequence", "text_map"} and _mode == "text":
        return _text_shape(value)

    if _mode in {"text_sequence", "public_sequence", "schema_sequence", "field_sequence", "pair_sequence"}:
        if not isinstance(value, (list, tuple, set, frozenset)):
            return _shape_only(value)
        if len(value) > _FINGERPRINT_MAX_ITEMS:
            raise ValueError("program fingerprint sequence exceeds item bound")
        child_mode = {
            "text_sequence": "text",
            "public_sequence": "public",
            "schema_sequence": "schema",
            "field_sequence": "field_pair",
            "pair_sequence": "pair",
        }[_mode]
        sequence = list(value)
        if isinstance(value, (set, frozenset)):
            sequence = sorted(sequence, key=lambda item: _key_text(item))
        return [
            _canonical_value(
                item,
                structural=structural,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode=child_mode,
            )
            for item in sequence
        ]

    if _mode == "field_pair" or _mode == "pair":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return _shape_only(value)
        second_mode = "field" if _mode == "field_pair" else "public"
        return [
            _canonical_value(
                value[0],
                structural=True,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode="public",
            ),
            _canonical_value(
                value[1],
                structural=True,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode=second_mode,
            ),
        ]

    if _mode == "public_endpoint":
        value = _public_endpoint(value)
        _mode = "public"
    if _mode == "text":
        return _text_shape(value)
    if _mode == "public":
        if isinstance(value, str):
            if len(value) > _FINGERPRINT_MAX_TEXT_CHARS:
                raise ValueError("program fingerprint text component exceeds bound")
            if _SECRET_TEXT_RE.search(value):
                return _text_shape(value)
            return value
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            if math.isfinite(value):
                return value
            return {"__nonfinite__": str(value)}
        if isinstance(value, Enum):
            return _canonical_value(
                value.value,
                structural=structural,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode="public",
            )
        # Public fields may be supplied as lightweight config objects (for
        # example a DSPy LM or Tool) rather than mappings. Project their
        # allow-listed public shape before falling back to type-only shape.
        if not isinstance(value, (Mapping, list, tuple, set, frozenset)) and not is_dataclass(value):
            object_shape = _object_components(value)
            if object_shape is not None:
                return _canonical_value(
                    object_shape,
                    structural=structural,
                    _active=_active,
                    _nodes=_nodes,
                    _depth=_depth + 1,
                    _mode=_mode,
                )
            return _shape_only(value)

    if isinstance(value, bytes):
        return _shape_only(value)

    container = isinstance(value, (Mapping, list, tuple, set, frozenset)) or (
        is_dataclass(value) and not isinstance(value, type)
    )
    marker = id(value) if container else None
    if marker is not None:
        if marker in _active:
            raise ValueError("program fingerprint components contain a cycle")
        _active.add(marker)
    try:
        if isinstance(value, Mapping):
            if len(value) > _FINGERPRINT_MAX_ITEMS:
                raise ValueError("program fingerprint mapping exceeds item bound")
            # Alias-based callers may pass a tool bundle as a mapping.  Detect
            # it before selecting child modes so names/descriptions/schemas
            # remain public shape while their text stays non-raw.
            effective_mode = _mode
            if _mode == "tools":
                names = {_normalized_key(_key_text(raw_key)) for raw_key in value}
                if "name" in names:
                    effective_mode = "tool"
                elif names & {"names", "descriptions", "schemas", "tool_names", "tool_descriptions", "tool_schemas"}:
                    effective_mode = "tools"
            if effective_mode == "fields":
                # Runner-produced Signature shapes wrap ordered field pairs in
                # ``inputs``/``outputs``; keep that wrapper and canonicalize
                # each sequence with its field-pair policy.  Direct callers
                # may instead pass a mapping from field name to metadata.
                keys = {_normalized_key(_key_text(raw_key)) for raw_key in value}
                if keys <= {"inputs", "outputs", "instructions"} and keys & {"inputs", "outputs"}:
                    effective_mode = "signature_shape"
                else:
                    # Signature field mappings are ordered compatibility inputs.
                    result: list[list[object]] = []
                    for raw_key, raw_value in value.items():
                        key = _key_text(raw_key)
                        if len(key) > _FINGERPRINT_MAX_TEXT_CHARS:
                            raise ValueError("program fingerprint key exceeds bound")
                        result.append(
                            [
                                key,
                                _canonical_value(
                                    raw_value,
                                    structural=True,
                                    _active=_active,
                                    _nodes=_nodes,
                                    _depth=_depth + 1,
                                    _mode="field",
                                ),
                            ]
                        )
                    return result
            normalized: dict[str, object] = {}
            for raw_key, raw_value in value.items():
                key = _key_text(raw_key)
                if len(key) > _FINGERPRINT_MAX_TEXT_CHARS:
                    raise ValueError("program fingerprint key exceeds bound")
                normalized_key = _normalized_key(key)
                # Structural field names may legally be called request,
                # history, or attachment; component-level turn values are
                # filtered before this function is called.
                if _is_excluded_key(key) and effective_mode in {
                    "root",
                    "lm",
                    "policy",
                    "contract",
                    "skill",
                    "tools",
                    "tool",
                }:
                    continue
                if _is_secret_key(key) and effective_mode not in {"fields", "field", "schema", "schema_fields"}:
                    continue
                child_mode = _canonical_child_mode(effective_mode, key, raw_value)
                if normalized_key in {"api_base", "base_url", "endpoint"}:
                    raw_value = _public_endpoint(raw_value)
                normalized[key] = _canonical_value(
                    raw_value,
                    structural=structural or effective_mode in {"fields", "field", "schema", "schema_fields", "tool"},
                    _active=_active,
                    _nodes=_nodes,
                    _depth=_depth + 1,
                    _mode=child_mode,
                )
            return {key: normalized[key] for key in sorted(normalized)}
        if isinstance(value, (list, tuple)):
            if len(value) > _FINGERPRINT_MAX_ITEMS:
                raise ValueError("program fingerprint sequence exceeds item bound")
            # A sequence under a structural container is still open-ended
            # unless its parent selected a typed sequence mode above.
            return [
                _canonical_value(
                    item,
                    structural=structural,
                    _active=_active,
                    _nodes=_nodes,
                    _depth=_depth + 1,
                    _mode=_mode,
                )
                for item in value
            ]
        if isinstance(value, (set, frozenset)):
            if len(value) > _FINGERPRINT_MAX_ITEMS:
                raise ValueError("program fingerprint set exceeds item bound")
            values = [
                _canonical_value(
                    item,
                    structural=structural,
                    _active=_active,
                    _nodes=_nodes,
                    _depth=_depth + 1,
                    _mode=_mode,
                )
                for item in value
            ]
            return sorted(values, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        if isinstance(value, Enum):
            return _canonical_value(
                value.value,
                structural=structural,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode=_mode,
            )
        if is_dataclass(value) and not isinstance(value, type):
            return _canonical_value(
                {item.name: getattr(value, item.name) for item in fields(value)},
                structural=structural,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode=_mode,
            )
        object_shape = _object_components(value)
        if object_shape is not None:
            return _canonical_value(
                object_shape,
                structural=structural,
                _active=_active,
                _nodes=_nodes,
                _depth=_depth + 1,
                _mode=_mode,
            )
        return _shape_only(value)
    finally:
        if marker is not None:
            _active.discard(marker)


def _canonical_components(components: Mapping[str, object]) -> Mapping[str, object]:
    """Build the only representation that participates in a fingerprint."""
    # ``dspy_version`` is mandatory in the digest even when callers omit it.
    merged: dict[str, object] = {"dspy_version": str(dspy.__version__)}
    for raw_key, value in components.items():
        key = _key_text(raw_key)
        normalized = _normalized_key(key)
        if normalized == "dspy_version":
            if value is not None and str(value) != str(dspy.__version__):
                raise ValueError("program fingerprint DSPy version does not match the installed version")
            continue
        if _is_excluded_key(key) or _is_secret_key(key):
            continue
        merged[key] = value
    canonical = _canonical_value(merged, _mode="root")
    if not isinstance(canonical, Mapping):  # pragma: no cover - internal invariant
        raise TypeError("canonical program components must be a mapping")
    return dict(cast(Mapping[str, object], canonical))


@dataclass(frozen=True, slots=True)
class ProgramFingerprintComponents:
    """Typed compatibility inputs for a resident Root RLM program.

    Only compatibility inputs belong in this object.  The explicitly named
    request/history/attachment/memory fields are accepted solely to make the
    exclusion contract visible to callers and are intentionally ignored by the
    digest helper.
    """

    dspy_version: str | None = None
    signature_fields: object = field(default_factory=dict)
    signature_instructions: object = ""
    root_lm_config: object = field(default_factory=dict)
    sub_lm_config: object = field(default_factory=dict)
    tools: object = field(default_factory=tuple)
    recursion_policy: object = field(default_factory=dict)
    limits: object = field(default_factory=dict)
    output_contract: object = field(default_factory=dict)
    skill_signature: object = field(default_factory=dict)
    skill_instructions: object = ""
    interpreter_protocol_version: str = ""

    # Helpful aliases for callers that already use the shorter P45 names.
    root_lm: object | None = None
    sub_lm: object | None = None
    signature_schema: object | None = None
    tool_names: object | None = None
    tool_descriptions: object | None = None
    tool_schemas: object | None = None
    recursion: object | None = None
    output_schema: object | None = None
    skill: object | None = None
    interpreter_version: str | None = None

    # Explicitly excluded turn/session values.
    request: object | None = None
    history: object | None = None
    attachment_ids: object | None = None
    memory: object | None = None
    secrets: object | None = None

    def as_mapping(self) -> dict[str, object]:
        """Return component fields using canonical names and aliases."""
        values = {item.name: getattr(self, item.name) for item in fields(self)}
        if values.get("dspy_version") is None:
            values.pop("dspy_version")
        aliases = {
            "root_lm": "root_lm_config",
            "sub_lm": "sub_lm_config",
            "signature_schema": "signature_fields",
            "recursion": "recursion_policy",
            "output_schema": "output_contract",
            "skill": "skill_signature",
            "interpreter_version": "interpreter_protocol_version",
        }
        for alias, canonical in aliases.items():
            value = values.get(alias)
            if value is not None:
                values[canonical] = value
        if (
            values.get("tool_names") is not None
            or values.get("tool_descriptions") is not None
            or values.get("tool_schemas") is not None
        ):
            values["tools"] = {
                "names": values.get("tool_names"),
                "descriptions": values.get("tool_descriptions"),
                "schemas": values.get("tool_schemas"),
            }
        return values


class ProgramFingerprint(str):
    """A validated SHA-256 digest used as the resident program identity."""

    def __new__(cls, value: str) -> ProgramFingerprint:
        if len(value) != hashlib.sha256().digest_size * 2:
            raise ValueError("program fingerprint must be a SHA-256 hex digest")
        try:
            int(value, 16)
        except ValueError:
            raise ValueError("program fingerprint must be a SHA-256 hex digest") from None
        return cast(ProgramFingerprint, super().__new__(cls, value.lower()))

    @property
    def digest(self) -> str:
        """Return the digest as a plain string."""
        return str(self)

    def hex_digest(self) -> str:
        """Return the digest using the explicit fingerprint API spelling."""
        return str(self)

    @property
    def hex(self) -> str:
        """Return the hexadecimal digest for callers using value semantics."""
        return str(self)

    @classmethod
    def from_components(
        cls,
        components: ProgramFingerprintComponents | Mapping[str, object] | None = None,
        **overrides: object,
    ) -> ProgramFingerprint:
        """Create a fingerprint from deterministic compatibility components."""
        return compute_program_fingerprint(components, **overrides)


def compute_program_fingerprint(
    components: ProgramFingerprintComponents | Mapping[str, object] | None = None,
    **overrides: object,
) -> ProgramFingerprint:
    """Return a stable SHA-256 fingerprint for one RLM program.

    The canonical payload contains exact DSPy version, Signature schema and
    instructions, Root/Sub LM shape, Tool shape, recursion policy, limits,
    output contract, selected Skill shape/instructions, and interpreter
    protocol version.  Request text, History bodies, Attachment/Memory data,
    and secret-bearing values are excluded before hashing.
    """
    if components is None:
        values: dict[str, object] = {}
    elif isinstance(components, ProgramFingerprintComponents):
        values = components.as_mapping()
    elif isinstance(components, Mapping):
        values = dict(components)
    else:  # pragma: no cover - guarded by the type contract
        raise TypeError("components must be a mapping or ProgramFingerprintComponents")
    values.update(overrides)
    canonical = _canonical_components(values)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ProgramFingerprint(hashlib.sha256(encoded).hexdigest())


def digest_program_components(
    components: ProgramFingerprintComponents | Mapping[str, object] | None = None,
    **overrides: object,
) -> ProgramFingerprint:
    """Alias emphasizing that the helper hashes canonical components."""
    return compute_program_fingerprint(components, **overrides)


# Friendly aliases used by callers during the P45 migration.
fingerprint_components = digest_program_components
program_fingerprint = compute_program_fingerprint


# ---------------------------------------------------------------------------
# Session state and provider-neutral cleanup
# ---------------------------------------------------------------------------


class RuntimeHealth(StrEnum):
    """Admission state for a resident Session runtime."""

    HEALTHY = "healthy"
    TAINTED = "tainted"
    DRAINING = "draining"
    CLOSED = "closed"
    FAILED = "failed"


class RuntimeUnavailableError(RuntimeError):
    """A resident runtime cannot accept another Turn execution."""


class CleanupCallback(Protocol):
    """Provider-owned cleanup hook for a resident Root lease/Sandbox."""

    def __call__(self, state: SessionRLMState) -> Awaitable[None] | None: ...


class RuntimeFactory(Protocol):
    """Factory for one new native RLM/interpreter runtime state."""

    def __call__(
        self,
        session_key: SessionKey,
        program_fingerprint: str,
    ) -> SessionRLMState | Awaitable[SessionRLMState]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _await_if_needed(value: object) -> None:
    if inspect.isawaitable(value):
        await value


async def _close_resource(resource: object) -> None:
    """Close one injected resource through a small provider-neutral protocol."""
    if resource is None:
        return
    if isinstance(resource, ThreadPoolExecutor):
        await asyncio.to_thread(resource.shutdown, wait=True, cancel_futures=True)
        return
    for method_name in ("aclose", "close", "shutdown"):
        method = getattr(resource, method_name, None)
        if callable(method):
            await _await_if_needed(method())
            return
    if callable(resource):
        callback = cast(Callable[[], object], resource)
        await _await_if_needed(callback())


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Full tenancy key for resident state: Workspace plus Session."""

    workspace_id: str
    session_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", str(self.workspace_id))
        object.__setattr__(self, "session_id", str(self.session_id))
        if not self.workspace_id or not self.session_id:
            raise ValueError("workspace_id and session_id must be non-empty")


@dataclass(slots=True)
class SessionRLMState:
    """Resident native Root RLM and caller-owned interpreter for one Session."""

    session_key: SessionKey
    program_fingerprint: str
    # DSPy exposes RLM dynamically in its runtime package; this field is the
    # exact native object but remains Any so ty supports DSPy 3.3.1 internals.
    rlm: Any
    interpreter: object
    # Attachment/broker context is a per-Turn reset boundary, not part of the
    # program fingerprint. A changed manifest forces a resident rotation.
    context_binding: str | None = None
    root_lease: object | None = None
    cleanup_handle: object | None = None
    # The Tool registry belongs to the resident program, not to the Runner
    # instance that first created it.  This lets another Runner safely reuse
    # a shared registry/state after the original Runner is closed.
    tool_registry: object | None = None
    worker_executor: ThreadPoolExecutor | None = None
    interpreter_owned_by_root: bool = False
    execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0
    last_used_at: datetime = field(default_factory=_utcnow)
    health: RuntimeHealth = RuntimeHealth.HEALTHY
    tainted: bool = False
    active: bool = False
    draining: bool = False
    _close_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _close_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _execution_lease: SessionRuntimeLease | None = field(default=None, init=False, repr=False)
    _inactive_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.last_used_at = _as_utc(self.last_used_at)
        self._inactive_event.set() if not self.active else self._inactive_event.clear()
        if self.tainted and self.health is RuntimeHealth.HEALTHY:
            self.health = RuntimeHealth.TAINTED

    @property
    def closed(self) -> bool:
        """Whether this state has completed its idempotent cleanup boundary."""
        return self._closed

    @property
    def healthy(self) -> bool:
        """Whether this state is eligible for compatible reuse."""
        return self.health is RuntimeHealth.HEALTHY and not self.tainted and not self.draining and not self._closed

    def touch(self, now: datetime | None = None) -> None:
        """Update resident-use time without changing interpreter namespace."""
        self.last_used_at = _as_utc(now or _utcnow())

    def mark_active(self, now: datetime | None = None) -> None:
        """Mark the state as serving one execution lane."""
        if self._closed or self.draining or self.tainted:
            raise RuntimeUnavailableError("Session RLM runtime is unavailable")
        self.active = True
        self._inactive_event.clear()
        self.touch(now)

    def mark_inactive(self, now: datetime | None = None) -> None:
        """Mark the execution lane idle and refresh its eviction timestamp."""
        self.active = False
        self._inactive_event.set()
        self.touch(now)

    def mark_tainted(self) -> None:
        """Prevent future reuse after an uncertain or failed Turn."""
        self.tainted = True
        if not self._closed:
            self.health = RuntimeHealth.TAINTED

    async def _wait_inactive(self) -> None:
        # The event covers registry-mediated execution.  The lock acquisition
        # also fences callers that directly use the documented asyncio.Lock.
        while self.active:
            await self._inactive_event.wait()
        async with self.execution_lock:
            return

    async def aclose(self, cleanup: CleanupCallback | None = None) -> None:
        """Close interpreter and provider resources under owned task control.

        The close task is shielded from caller cancellation.  A cancelled
        registry/eviction caller must not strand a worker, interpreter, or
        provider lease after it has marked this state draining.
        """
        async with self._close_lock:
            if self._closed:
                return
            self.draining = True
            self.health = RuntimeHealth.DRAINING
            task = self._close_task
            if task is None:
                task = asyncio.create_task(
                    self._close_impl(cleanup),
                    name="fleet-session-runtime-state-close",
                )
                self._close_task = task
        try:
            await asyncio.shield(task)
        except BaseException:
            # A caller cancellation only cancels this waiter; the shielded
            # close task remains the owner.  Clear the single-flight handle
            # only after the close task itself has settled, so registry
            # shutdown can retain and quarantine a still-running cleanup.
            if task.done():
                async with self._close_lock:
                    if self._close_task is task:
                        self._close_task = None
            raise

    async def _close_impl(self, cleanup: CleanupCallback | None = None) -> None:
        """Run the state close once; ownership lives in ``_close_task``."""
        await self._wait_inactive()
        # A provider-owned root may be handed from one compatible
        # program generation to the next.  Its interpreter must remain
        # open even during the old state's cleanup; the new state owns
        # the transferred root lease.
        interpreter_resource = None if self.interpreter_owned_by_root else self.interpreter
        resources = (self.worker_executor, interpreter_resource, self.root_lease, self.cleanup_handle)
        seen: set[int] = set()
        errors: list[BaseException] = []
        resource_errors: list[BaseException] = []
        for resource in resources:
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            try:
                await _close_resource(resource)
            except BaseException as exc:
                errors.append(exc)
                resource_errors.append(exc)
        if cleanup is not None:
            try:
                await _await_if_needed(cleanup(self))
            except BaseException as exc:
                errors.append(exc)
        self.active = False
        self._inactive_event.set()
        if errors:
            # Keep resource/provider failures open-but-tainted so their exact
            # owners remain available to a retry. A post-resource hook failure
            # is safe to publish as closed because all owned resources settled.
            self.tainted = True
            self.health = RuntimeHealth.FAILED
            # Post-close diagnostic hooks can fail after every owned resource
            # has settled. Preserve the historical closed boundary in that
            # narrow case; resource/provider failures keep the state open and
            # strongly owned for a later retry.
            self._closed = not resource_errors
            raise errors[0]
        self._closed = True
        self.health = RuntimeHealth.CLOSED


@dataclass(slots=True)
class SessionRuntimeLease:
    """Generation-safe execution token held from RLM start through cleanup.

    The registry lock protects admission and rotation; this token owns the
    per-session execution lock.  A caller must mark the outcome committed or
    tainted before releasing it.  Releasing an unsettled token taints the
    resident state fail-closed.
    """

    registry: SessionRLMRegistry
    state: SessionRLMState
    generation: int
    _released: bool = field(default=False, init=False, repr=False)
    _settled: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _turn_binding: object | None = field(default=None, init=False, repr=False)
    _turn_observer_cleanup: Callable[[], object] | None = field(default=None, init=False, repr=False)

    @property
    def session_key(self) -> SessionKey:
        """Return the tenancy key owned by this execution token."""
        return self.state.session_key

    @property
    def released(self) -> bool:
        """Whether the execution lane has been returned to the registry."""
        return self._released

    @property
    def committed(self) -> bool:
        """Whether the durable Turn settlement was confirmed."""
        return self._committed

    @property
    def settled(self) -> bool:
        """Whether this token has a terminal clean/tainted decision."""
        return self._settled

    def mark_committed(self) -> None:
        """Allow compatible reuse after the caller confirms durable commit."""
        if self._released:
            raise RuntimeUnavailableError("Session runtime lease is already released")
        if self._settled:
            if self._committed:
                return
            raise RuntimeUnavailableError("Session runtime lease is already tainted")
        if not self.state.healthy:
            raise RuntimeUnavailableError("Session RLM runtime is unavailable")
        self._settled = True
        self._committed = True

    def bind_turn_cleanup(
        self,
        binding: object,
        observer_cleanup: Callable[[], object] | None = None,
    ) -> None:
        """Attach stale-safe current-Turn bindings and observer cleanup to this token."""
        if self._released:
            raise RuntimeUnavailableError("Session runtime lease is already released")
        self._turn_binding = binding
        self._turn_observer_cleanup = observer_cleanup

    def mark_tainted(self) -> None:
        """Prevent reuse after any uncertain or failed execution boundary."""
        if self._released:
            return
        self._settled = True
        self._committed = False
        self.registry._mark_tainted_token(self)

    async def release(self) -> None:
        """Return the lane exactly once, tainting an undecided execution."""
        if self._released:
            return
        if not self._settled:
            self.mark_tainted()
        binding = self._turn_binding
        self._turn_binding = None
        observer_cleanup = self._turn_observer_cleanup
        self._turn_observer_cleanup = None
        cleanup_error: BaseException | None = None
        try:
            if observer_cleanup is not None:
                result = observer_cleanup()
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise RuntimeError("Turn observer cleanup must be synchronous")
        except BaseException as exc:
            cleanup_error = exc
            self.mark_tainted()
        try:
            remove = getattr(binding, "remove", None) if binding is not None else None
            if callable(remove):
                remove()
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
            self.mark_tainted()
        finally:
            self.state.mark_inactive(self.registry._clock())
            if self.state._execution_lease is self:
                self.state._execution_lease = None
            if self.state.execution_lock.locked():
                self.state.execution_lock.release()
            self._released = True
        if cleanup_error is not None:
            raise cleanup_error

    async def aclose(self) -> None:
        """Async cleanup spelling used by stream/lifecycle owners."""
        await self.release()

    async def __aenter__(self) -> SessionRuntimeLease:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.release()


# ---------------------------------------------------------------------------
# Async resident registry
# ---------------------------------------------------------------------------


StateOrKey: TypeAlias = SessionRLMState | SessionKey


class SessionRLMRegistry:
    """Coordinate compatible resident Root runtimes by full Session key."""

    def __init__(
        self,
        factory: RuntimeFactory | None = None,
        *,
        cleanup: CleanupCallback | None = None,
        idle_timeout: float | None = None,
        clock: Callable[[], datetime] = _utcnow,
        event_sink: Callable[[str, Mapping[str, str]], None] | None = None,
    ) -> None:
        if idle_timeout is not None and idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        self._factory = factory
        self._cleanup = cleanup
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._event_sink = event_sink
        self._event_counts: Counter[str] = Counter()
        self._close_observers: list[Callable[[SessionRLMState], None]] = []
        self._deferred_close_tasks: set[asyncio.Task[None]] = set()
        # A close that completed with an error is removed from admission but
        # remains strongly owned here for a later retry.
        self._quarantined_states: dict[SessionKey, SessionRLMState] = {}
        # Detached root leases from a failed handoff/factory remain strongly
        # owned until a later registry shutdown retry can close them.
        self._orphaned_resources: dict[int, object] = {}
        # Factories may be in flight before a state is published.  Retain
        # their tasks so shutdown can fence their post-factory publication.
        self._acquisition_tasks: set[asyncio.Task[Any]] = set()
        self._states: dict[SessionKey, SessionRLMState] = {}
        self._key_gates: dict[SessionKey, asyncio.Lock] = {}
        self._generations: dict[SessionKey, int] = {}
        self._shutdown = False

    @asynccontextmanager
    async def _track_acquisition(self) -> AsyncIterator[None]:
        """Retain an acquisition task until no state can be published."""
        task = asyncio.current_task()
        if task is not None:
            self._acquisition_tasks.add(task)
        try:
            yield
        finally:
            if task is not None:
                self._acquisition_tasks.discard(task)

    def _gate(self, key: SessionKey) -> asyncio.Lock:
        gate = self._key_gates.get(key)
        if gate is None:
            gate = asyncio.Lock()
            self._key_gates[key] = gate
        return gate

    def _prune_key(self, key: SessionKey) -> None:
        """Drop idle coordination metadata without racing a waiter."""
        if key in self._states:
            return
        gate = self._key_gates.get(key)
        if gate is None or gate.locked():
            return
        waiters = getattr(gate, "_waiters", ()) or ()
        if any(not waiter.cancelled() for waiter in waiters):
            return
        self._key_gates.pop(key, None)
        self._generations.pop(key, None)

    @asynccontextmanager
    async def _key_gate(self, key: SessionKey) -> AsyncIterator[None]:
        """Own one key gate and prune it only after the lock is released."""
        gate = self._gate(key)
        try:
            async with gate:
                yield
        finally:
            self._prune_key(key)

    def add_close_observer(self, observer: Callable[[SessionRLMState], None]) -> Callable[[], None]:
        """Register a local cleanup observer and return its removal callback."""
        self._close_observers.append(observer)

        def remove() -> None:
            with suppress(ValueError):
                self._close_observers.remove(observer)

        return remove

    def _notify_closed(self, state: SessionRLMState) -> None:
        """Notify local owners without allowing diagnostics to affect cleanup."""
        for observer in tuple(self._close_observers):
            with suppress(BaseException):
                observer(state)

    def _emit(self, event: str, key: SessionKey, **extra: str) -> None:
        """Record bounded internal lifecycle telemetry without request data."""
        self._event_counts[event] += 1
        payload = {"workspace_id": key.workspace_id, "session_id": key.session_id, **extra}
        sink = self._event_sink
        if sink is not None:
            with suppress(Exception):
                sink(event, payload)

    @property
    def has_deferred_closes(self) -> bool:
        """Whether a bounded shutdown left state-owned cleanup in flight."""
        return bool(
            self._deferred_close_tasks
            or self._quarantined_states
            or self._orphaned_resources
            or self._acquisition_tasks
            or any(not state.closed for state in self._states.values())
        )

    async def wait_deferred_closes(self, *, timeout: float | None = None) -> bool:
        """Drain deferred close ownership, returning whether all work settled."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        current_loop = asyncio.get_running_loop()
        while self._deferred_close_tasks or self._acquisition_tasks:
            tasks = tuple({*self._deferred_close_tasks, *self._acquisition_tasks})
            # A registry may be retained by a composition handoff after its
            # original loop has stopped.  Never pass foreign-loop Tasks to
            # ``asyncio.wait``; unresolved ownership is the safe result.
            if any(task.get_loop() is not current_loop for task in tasks):
                return False
            if deadline is None:
                await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
                continue
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            _, pending = await asyncio.wait(tasks, timeout=remaining)
            if pending:
                return False
        return not (
            self._quarantined_states
            or self._orphaned_resources
            or self._acquisition_tasks
            or any(not state.closed for state in self._states.values())
        )

    def observability_snapshot(self) -> Mapping[str, int]:
        """Return sanitized lifecycle counters for internal diagnostics."""
        return MappingProxyType(dict(self._event_counts))

    @staticmethod
    def _key(value: StateOrKey) -> SessionKey:
        return value.session_key if isinstance(value, SessionRLMState) else value

    @staticmethod
    def _fingerprint(value: str | ProgramFingerprint | Mapping[str, object]) -> str:
        if isinstance(value, Mapping):
            return str(compute_program_fingerprint(value))
        return str(value)

    def get(self, session_key: SessionKey) -> SessionRLMState | None:
        """Return a resident state without acquiring or creating one."""
        return self._states.get(session_key)

    def snapshot(self) -> Mapping[SessionKey, SessionRLMState]:
        """Return a read-only resident-state view for diagnostics/tests."""
        return MappingProxyType(dict(self._states))

    def __len__(self) -> int:
        return len(self._states)

    def _next_generation(self, key: SessionKey) -> int:
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        return generation

    async def _invoke_factory(self, factory: RuntimeFactory, key: SessionKey, fingerprint: str) -> SessionRLMState:
        created = factory(key, fingerprint)
        if inspect.isawaitable(created):
            created = await cast(Awaitable[SessionRLMState], created)
        if not isinstance(created, SessionRLMState):
            raise TypeError("Session RLM factory must return SessionRLMState")
        if created.session_key != key:
            raise ValueError("Session RLM factory returned a state for another Session")
        return created

    async def _acquire_locked(
        self,
        key: SessionKey,
        fingerprint: str,
        selected_factory: RuntimeFactory,
        context_binding: str | None = None,
        preserve_interpreter: object | None = None,
    ) -> SessionRLMState:
        """Acquire or rotate state while the caller owns ``key``'s gate."""
        existing = self._states.get(key)
        compatible_context = context_binding is None or existing is None or existing.context_binding == context_binding
        if (
            existing is not None
            and existing.program_fingerprint == fingerprint
            and compatible_context
            and existing.healthy
        ):
            existing.touch(self._clock())
            self._emit("session_runtime_reused", key, generation=str(existing.generation))
            return existing

        handoff_root: object | None = None
        if existing is not None:
            self._emit("program_fingerprint_changed", key)
            self._emit("session_runtime_rotated", key, generation=str(existing.generation))
            # A clean fingerprint-only rotation can reuse the provider root
            # and its interpreter.  Detach the root lease before closing the
            # old program so its cleanup cannot close the interpreter that the
            # replacement factory is about to use.  Context/manifest changes
            # deliberately do not take this path: their provider has already
            # supplied a different interpreter.
            if (
                preserve_interpreter is not None
                and existing.healthy
                and existing.interpreter is preserve_interpreter
                and existing.context_binding == context_binding
                and existing.interpreter_owned_by_root
            ):
                handoff_root = existing.root_lease
                existing.root_lease = None
            existing.draining = True
            try:
                await self._close_registered(key, existing)
            except BaseException:
                if handoff_root is not None:
                    # Restore the detached root on the still-owned old state
                    # before attempting a best-effort close. If that attempt
                    # fails, the state/root remains visible to shutdown retry.
                    existing.root_lease = handoff_root
                    try:
                        await _close_resource(handoff_root)
                    except BaseException:
                        if existing.closed:
                            self._orphaned_resources[id(handoff_root)] = handoff_root
                raise

        try:
            created = await self._invoke_factory(selected_factory, key, fingerprint)
        except BaseException:
            if handoff_root is not None:
                try:
                    await _close_resource(handoff_root)
                except BaseException:
                    self._orphaned_resources[id(handoff_root)] = handoff_root
            raise
        if self._shutdown:
            # Shutdown may begin while an async factory is in flight.  Never
            # publish the returned state after that boundary; close it under
            # the factory owner's task and reject the acquisition.
            try:
                await created.aclose(self._cleanup)
            except BaseException:
                self._quarantined_states[key] = created
            if handoff_root is not None:
                try:
                    await _close_resource(handoff_root)
                except BaseException:
                    self._orphaned_resources[id(handoff_root)] = handoff_root
            raise RuntimeUnavailableError("Session RLM registry is shut down")
        if handoff_root is not None:
            # The runner factory receives the same interpreter but a fresh
            # per-Turn release wrapper.  Keep the old resident root lease as
            # the replacement state's close resource.
            created.root_lease = handoff_root
            created.interpreter_owned_by_root = True
        created.program_fingerprint = fingerprint
        if context_binding is not None:
            created.context_binding = context_binding
        created.generation = self._next_generation(key)
        created.last_used_at = _as_utc(self._clock())
        created.health = RuntimeHealth.HEALTHY
        created.tainted = False
        created.active = False
        created.draining = False
        self._states[key] = created
        self._emit("session_runtime_created", key, generation=str(created.generation))
        return created

    async def acquire(
        self,
        session_key: SessionKey,
        program_fingerprint: str | ProgramFingerprint | Mapping[str, object],
        factory: RuntimeFactory | None = None,
    ) -> SessionRLMState:
        """Reuse or create one compatible resident state.

        A per-key gate surrounds both compatibility inspection and factory
        execution.  Consequently two concurrent callers for one Session can
        never create overlapping RLM/interpreter states.  The returned state
        is not execution-locked; use :meth:`acquire_execution` for a Turn lane.
        """
        async with self._track_acquisition():
            if self._shutdown:
                raise RuntimeUnavailableError("Session RLM registry is shut down")
            key = session_key
            fingerprint = self._fingerprint(program_fingerprint)
            selected_factory = factory or self._factory
            if selected_factory is None:
                raise ValueError("a Session RLM factory is required")

            async with self._key_gate(key):
                if self._shutdown:
                    raise RuntimeUnavailableError("Session RLM registry is shut down")
                state = await self._acquire_locked(key, fingerprint, selected_factory)
                if self._shutdown:
                    await self._close_registered(key, state)
                    raise RuntimeUnavailableError("Session RLM registry is shut down")
                return state

    async def acquire_execution(
        self,
        session_key: SessionKey,
        program_fingerprint: str | ProgramFingerprint | Mapping[str, object],
        factory: RuntimeFactory | None = None,
        *,
        context_binding: str | None = None,
        preserve_interpreter: object | None = None,
    ) -> SessionRuntimeLease:
        """Acquire a compatible state and own its serialized Turn lane.

        The per-key gate remains held while the execution lock is acquired.
        This prevents eviction or fingerprint rotation from racing the gap
        between state selection and actual execution ownership.
        """
        async with self._track_acquisition():
            if self._shutdown:
                raise RuntimeUnavailableError("Session RLM registry is shut down")
            key = session_key
            fingerprint = self._fingerprint(program_fingerprint)
            selected_factory = factory or self._factory
            if selected_factory is None:
                raise ValueError("a Session RLM factory is required")
            async with self._key_gate(key):
                if self._shutdown:
                    raise RuntimeUnavailableError("Session RLM registry is shut down")
                state = await self._acquire_locked(
                    key,
                    fingerprint,
                    selected_factory,
                    context_binding=context_binding,
                    preserve_interpreter=preserve_interpreter,
                )
                if self._shutdown:
                    await self._close_registered(key, state)
                    raise RuntimeUnavailableError("Session RLM registry is shut down")
                await state.execution_lock.acquire()
                try:
                    state.mark_active(self._clock())
                except BaseException:
                    state.execution_lock.release()
                    raise
                lease = SessionRuntimeLease(self, state, state.generation)
                state._execution_lease = lease
                return lease

    async def release(self, state_or_key: StateOrKey) -> None:
        """Release a checkout, including legacy calls with a leased state.

        ``acquire_execution`` returns a generation-owned lease, but older
        callers sometimes pass its ``state`` to this method.  Delegate to the
        exact owner when present; never unlock an execution lock belonging to
        an unknown direct ``execution()`` caller.
        """
        state = state_or_key if isinstance(state_or_key, SessionRLMState) else self._states.get(state_or_key)
        if state is None:
            return
        lease = state._execution_lease
        if lease is not None:
            await lease.release()
            return
        if state.execution_lock.locked():
            raise RuntimeUnavailableError("execution lock has no releasable Session lease")
        state.mark_inactive(self._clock())

    def _mark_tainted_token(self, token: SessionRuntimeLease) -> None:
        """Taint only the generation still owned by ``token``."""
        current = self._states.get(token.state.session_key)
        if current is token.state and current.generation == token.generation:
            current.mark_tainted()
            self._emit("session_runtime_tainted", token.state.session_key, generation=str(token.generation))

    def mark_tainted(self, state_or_key: StateOrKey) -> None:
        """Mark resident state tainted; the next acquire rotates it."""
        state = state_or_key if isinstance(state_or_key, SessionRLMState) else self._states.get(state_or_key)
        if state is not None:
            state.mark_tainted()
            self._emit("session_runtime_tainted", state.session_key, generation=str(state.generation))

    def mark_committed(self, state_or_lease: SessionRLMState | SessionRuntimeLease) -> None:
        """Record a clean durable outcome without releasing an active lane."""
        if isinstance(state_or_lease, SessionRuntimeLease):
            state_or_lease.mark_committed()

    async def _close_registered(self, key: SessionKey, state: SessionRLMState | None) -> None:
        """Close one exact state without dropping cancellation-owned cleanup."""
        if state is None:
            return
        state.draining = True
        try:
            await state.aclose(self._cleanup)
        except asyncio.CancelledError:
            close_task = state._close_task
            if close_task is not None and not close_task.done():
                self._remember_deferred_close(close_task, key)
                await self._quarantine_shutdown_state(key, state)
            elif state.closed:
                if self._states.get(key) is state:
                    self._notify_closed(state)
                    self._states.pop(key, None)
            else:
                # A close task can itself have been cancelled before it could
                # publish a retryable handle. Keep the exact state quarantined
                # instead of dropping a possibly-live interpreter/provider.
                await self._quarantine_shutdown_state(key, state)
            self._emit("session_runtime_close_cancelled", key, generation=str(state.generation))
            raise
        except BaseException:
            self._emit("session_runtime_close_failed", key, generation=str(state.generation))
            # A failed close is still an owned state. Leave it in the registry
            # (tainted and ineligible for admission) so the next shutdown or
            # unhealthy-rotation call can retry its exact cleanup.
            if state.closed:
                self._quarantined_states.pop(key, None)
                if self._states.get(key) is state:
                    self._notify_closed(state)
                    self._states.pop(key, None)
            raise
        else:
            self._quarantined_states.pop(key, None)
            if self._states.get(key) is state:
                self._notify_closed(state)
                self._states.pop(key, None)

    async def _close_registered_bounded(
        self,
        key: SessionKey,
        state: SessionRLMState,
        *,
        deadline: float | None = None,
    ) -> None:
        """Close an exact state, retaining ownership when a bound expires."""
        task = asyncio.create_task(
            self._close_registered(key, state),
            name="fleet-session-runtime-close",
        )
        try:
            if deadline is None:
                await asyncio.shield(task)
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            if task.done():
                task.result()
            else:
                self._remember_deferred_close(task, key)
                await self._quarantine_shutdown_state(key, state)
        except asyncio.CancelledError:
            if not task.done():
                self._remember_deferred_close(task, key)
                await self._quarantine_shutdown_state(key, state)
            elif not state.closed:
                await self._quarantine_shutdown_state(key, state)
            raise

    async def close_unhealthy(
        self,
        session_key: SessionKey,
        *,
        deadline: float | None = None,
    ) -> bool:
        """Close a tainted resident before the next Turn is prepared.

        Providers may acquire their sandbox before the exact program
        fingerprint is known. This hook lets preparation retire a prior
        failed resident early, so its provider lease cannot be borrowed for
        the next Turn. Healthy state is left untouched.
        """
        async with self._key_gate(session_key):
            state = self._states.get(session_key)
            if state is None or state.healthy:
                return False
            await self._close_registered_bounded(session_key, state, deadline=deadline)
            return True

    async def close(
        self,
        state_or_key: StateOrKey,
        *,
        drain_seconds: float | None = None,
    ) -> None:
        """Remove and close one resident state with optional bounded draining."""
        key = self._key(state_or_key)
        deadline = None
        if drain_seconds is not None:
            if drain_seconds < 0:
                raise ValueError("drain_seconds must be non-negative")
            deadline = asyncio.get_running_loop().time() + drain_seconds
        async with self._key_gate(key):
            state = state_or_key if isinstance(state_or_key, SessionRLMState) else self._states.get(key)
            if state is not None:
                await self._close_registered_bounded(key, state, deadline=deadline)

    async def aclose(
        self,
        state_or_key: StateOrKey,
        *,
        drain_seconds: float | None = None,
    ) -> None:
        """Async spelling of :meth:`close` for lifecycle integrations."""
        await self.close(state_or_key, drain_seconds=drain_seconds)

    async def delete_session(
        self,
        session_key: SessionKey,
        *,
        drain_seconds: float | None = None,
    ) -> None:
        """Explicitly delete one Session's resident runtime."""
        await self.close(session_key, drain_seconds=drain_seconds)

    async def remove_session(
        self,
        session_key: SessionKey,
        *,
        drain_seconds: float | None = None,
    ) -> None:
        """Alias for explicit Session deletion."""
        await self.delete_session(session_key, drain_seconds=drain_seconds)

    @asynccontextmanager
    async def execution(
        self,
        state_or_key: StateOrKey,
        program_fingerprint: str | ProgramFingerprint | Mapping[str, object] | None = None,
        factory: RuntimeFactory | None = None,
    ) -> AsyncIterator[SessionRLMState]:
        """Serialize one Turn execution for a resident Session.

        Passing a key acquires/reuses state first.  Passing an already acquired
        state avoids another lookup.  Ordinary interpreter globals are never
        reset here; only current-Turn orchestration outside this core may bind
        transient names.
        """
        if isinstance(state_or_key, SessionRLMState):
            state = state_or_key
        else:
            if program_fingerprint is None:
                raise ValueError("program_fingerprint is required when execution receives a SessionKey")
            state = await self.acquire(state_or_key, program_fingerprint, factory)
        if not state.healthy:
            raise RuntimeUnavailableError("Session RLM runtime is unavailable")
        async with state.execution_lock:
            if not state.healthy:
                raise RuntimeUnavailableError("Session RLM runtime is unavailable")
            state.mark_active(self._clock())
            try:
                yield state
            finally:
                state.mark_inactive(self._clock())

    @asynccontextmanager
    async def checkout(
        self,
        session_key: SessionKey,
        program_fingerprint: str | ProgramFingerprint | Mapping[str, object],
        factory: RuntimeFactory | None = None,
    ) -> AsyncIterator[SessionRLMState]:
        """Acquire and execution-lock one Session in one context manager."""
        async with self.execution(session_key, program_fingerprint, factory) as state:
            yield state

    async def evict_configured_idle(self, *, deadline: float | None = None) -> tuple[SessionKey, ...]:
        """Evict configured idle states, or do nothing when no policy is set."""
        if self._idle_timeout is None:
            return ()
        return await self.evict_idle(self._idle_timeout, deadline=deadline)

    @staticmethod
    def _defer_eviction(state: SessionRLMState) -> None:
        """Return an active state to admission when idle eviction misses its deadline."""
        if state.closed:
            return
        state.draining = False
        state.health = RuntimeHealth.TAINTED if state.tainted else RuntimeHealth.HEALTHY

    async def evict_idle(
        self,
        idle_seconds: float | None = None,
        *,
        now: datetime | None = None,
        deadline: float | None = None,
    ) -> tuple[SessionKey, ...]:
        """Close resident states idle for at least ``idle_seconds``.

        A state is marked draining before its execution lock is awaited.  If a
        Turn is active, eviction therefore waits for that Turn to leave the
        lane before closing the interpreter and injected provider resources.
        When preparation supplies a deadline, the active-turn wait is bounded
        and an eviction that cannot start in time is deferred rather than
        extending the next Turn past its preparation budget.
        """
        timeout = self._idle_timeout if idle_seconds is None else idle_seconds
        if timeout is None or timeout <= 0:
            raise ValueError("idle_seconds must be positive")
        current = _as_utc(now or self._clock())
        cutoff = current - timedelta(seconds=timeout)
        candidates = [
            (key, state)
            for key, state in tuple(self._states.items())
            if not state.draining and state.last_used_at <= cutoff
        ]
        evicted: list[SessionKey] = []
        for key, candidate in candidates:
            async with self._key_gate(key):
                if self._states.get(key) is not candidate:
                    continue
                # Recheck after waiting for the per-key gate so a concurrent
                # execution that refreshed last_used_at is not evicted.
                if candidate.draining or candidate.last_used_at > cutoff:
                    continue
                candidate.draining = True
                if candidate.active and deadline is not None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        self._defer_eviction(candidate)
                        continue
                    try:
                        await asyncio.wait_for(candidate._wait_inactive(), timeout=remaining)
                    except TimeoutError:
                        self._defer_eviction(candidate)
                        continue
                    except asyncio.CancelledError:
                        self._defer_eviction(candidate)
                        raise
                await self._close_registered(key, candidate)
                self._emit("session_runtime_evicted", key, generation=str(candidate.generation))
                evicted.append(key)
        return tuple(evicted)

    def _remember_deferred_close(self, task: asyncio.Task[None], key: SessionKey) -> None:
        """Keep timed-out close ownership and consume its eventual outcome."""
        self._deferred_close_tasks.add(task)

        def settled(completed: asyncio.Task[None]) -> None:
            self._deferred_close_tasks.discard(completed)
            if completed.cancelled():
                self._emit("session_runtime_deferred_close_cancelled", key)
                return
            try:
                error = completed.exception()
            except BaseException as exc:
                error = exc
            if error is None:
                self._quarantined_states.pop(key, None)
            else:
                self._emit("session_runtime_deferred_close_failed", key, error_type=type(error).__name__)
                logger.warning(
                    "deferred Session runtime close failed",
                    extra={"workspace_id": key.workspace_id, "session_id": key.session_id},
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(settled)

    async def _quarantine_shutdown_state(self, key: SessionKey, state: SessionRLMState) -> None:
        """Remove a stuck state from admission while its close task remains owned."""
        state.mark_tainted()
        self._quarantined_states[key] = state
        if self._states.get(key) is state:
            self._states.pop(key, None)
            self._notify_closed(state)
        self._emit("session_runtime_quarantined", key, generation=str(state.generation))

    async def _close_key(
        self,
        key: SessionKey,
        expected: SessionRLMState | None,
    ) -> None:
        """Acquire the per-key gate before closing an exact resident state."""
        async with self._key_gate(key):
            target = expected if expected is not None else self._states.get(key)
            if target is not None:
                await self._close_registered(key, target)

    async def shutdown(self, *, drain_seconds: float | None = None) -> None:
        """Close resident runtimes without racing in-flight acquisitions.

        Shutdown first fences factory/acquisition tasks, then closes each
        exact state behind its per-key gate.  A bounded wait quarantines a
        state and retains the gate-fenced close task until it settles.
        """
        self._shutdown = True
        errors: list[BaseException] = []
        deadline = None
        if drain_seconds is not None:
            if drain_seconds < 0:
                raise ValueError("drain_seconds must be non-negative")
            deadline = asyncio.get_running_loop().time() + drain_seconds

        current = asyncio.current_task()
        acquisitions = tuple(task for task in self._acquisition_tasks if task is not current)
        if acquisitions:
            if deadline is None:
                await asyncio.gather(
                    *(asyncio.shield(task) for task in acquisitions),
                    return_exceptions=True,
                )
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    _, pending = await asyncio.wait(acquisitions, timeout=remaining)
                    if pending:
                        logger.warning(
                            "Session runtime acquisition drain expired with %d owned job(s)",
                            len(pending),
                        )
                else:
                    logger.warning(
                        "Session runtime acquisition drain expired with %d owned job(s)",
                        len(acquisitions),
                    )

        # Capture exact states after the acquisition fence.  No new state can
        # be published once ``_shutdown`` is set; an acquisition that was
        # already in a factory rechecks that boundary before publication.
        states = dict(self._quarantined_states)
        states.update(self._states)
        for key, state in tuple(states.items()):
            close_task = asyncio.create_task(
                self._close_key(key, state),
                name="fleet-session-runtime-close",
            )
            try:
                if deadline is None:
                    await asyncio.shield(close_task)
                else:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError
                    await asyncio.wait_for(asyncio.shield(close_task), timeout=remaining)
            except TimeoutError:
                # Distinguish the shutdown wait expiring from a close task
                # that itself reported a provider TimeoutError.  A completed
                # task has no deferred ownership left to quarantine.
                if close_task.done():
                    try:
                        close_task.result()
                    except BaseException as exc:
                        errors.append(exc)
                else:
                    self._remember_deferred_close(close_task, key)
                    await self._quarantine_shutdown_state(key, state)
            except asyncio.CancelledError:
                # Shutdown cancellation must not cancel the shielded close.
                # Retain the exact task/state before propagating cancellation.
                if not close_task.done():
                    self._remember_deferred_close(close_task, key)
                    await self._quarantine_shutdown_state(key, state)
                elif not state.closed:
                    await self._quarantine_shutdown_state(key, state)
                raise
            except BaseException as exc:
                errors.append(exc)

        if self._orphaned_resources:
            for resource_id, resource in tuple(self._orphaned_resources.items()):
                close_task = asyncio.create_task(_close_resource(resource), name="fleet-session-runtime-orphan-close")
                try:
                    if deadline is None:
                        await asyncio.shield(close_task)
                    else:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise TimeoutError
                        await asyncio.wait_for(asyncio.shield(close_task), timeout=remaining)
                except TimeoutError:
                    # Keep the resource strongly owned for the next shutdown
                    # retry; its own close method is idempotent/single-flight.
                    continue
                except BaseException as exc:
                    errors.append(exc)
                else:
                    self._orphaned_resources.pop(resource_id, None)

        if self._deferred_close_tasks:
            if deadline is None:
                await asyncio.gather(
                    *(asyncio.shield(task) for task in tuple(self._deferred_close_tasks)),
                    return_exceptions=True,
                )
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    await asyncio.wait(tuple(self._deferred_close_tasks), timeout=remaining)
        if errors:
            raise errors[0]

    async def __aenter__(self) -> SessionRLMRegistry:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.shutdown()


# Public migration alias: callers may refer to the resident object as either
# an RLM registry or a Session runtime registry.
SessionRuntimeRegistry = SessionRLMRegistry


# ---------------------------------------------------------------------------
# Stable, provider-neutral Tool proxies for reusable Session interpreters
# ---------------------------------------------------------------------------

# Retained for import compatibility with the pre-P45 proxy API.  These values
# no longer constrain model-facing Tool metadata: the Session proxy snapshots
# the frozen source contract exactly and sanitizes only event/result payloads.
MAX_TOOL_NAME_CHARS = 96
MAX_TOOL_DESCRIPTION_CHARS = 512
MAX_TOOL_ARGUMENTS = 32
MAX_TOOL_ARGUMENT_NAME_CHARS = 96
MAX_TOOL_ARGUMENT_DESCRIPTION_CHARS = 256
MAX_TOOL_SCHEMA_BYTES = 8_192
MAX_SESSION_TOOL_COUNT = 64
MAX_SESSION_ID_CHARS = 128


class SessionToolAuthorizationError(RuntimeError):
    """Closed failure raised when a retained Tool is not authorized now."""

    public_message = "Tool is not authorized for this Turn"

    def __init__(self) -> None:
        super().__init__(self.public_message)


# A more specific spelling is useful at integration seams while retaining one
# public failure shape for all fail-closed cases.
SessionToolUnavailableError = SessionToolAuthorizationError


class ToolAuthorization(Protocol):
    """Optional structural authorization object accepted by ``install``."""

    def is_live(self) -> bool: ...

    def allows(self, tool_name: str) -> bool: ...


ClaimCheck: TypeAlias = Callable[..., bool]
AuthorizationCheck: TypeAlias = Callable[..., bool]


def _identity_key(value: object, *, label: str, max_chars: int = MAX_SESSION_ID_CHARS) -> tuple[str, str | int]:
    """Create a bounded comparison key without retaining an opaque object.

    Run and Session ids in Fleet are normally UUIDs or strings.  The identity
    fallback keeps this small provider-neutral seam useful for tests and
    adapters that use an opaque object while avoiding a strong reference to
    that object (which could itself contain authorization or payload state).
    """
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a stable id")
    if isinstance(value, UUID):
        return ("uuid", str(value))
    if isinstance(value, str):
        if not value or len(value) > max_chars:
            raise ValueError(f"{label} is invalid")
        return ("str", value)
    if isinstance(value, int):
        return ("int", str(value))
    if value is None:
        raise TypeError(f"{label} must be a stable id")
    # ``id`` is intentionally used instead of repr/str: arbitrary objects
    # may contain secrets, and converting them would retain those values in
    # the registry's state or in an exception.
    return ("object", id(value))


def _identity_label(key: tuple[str, str | int]) -> str:
    """Return a bounded non-sensitive label for diagnostics/lease state."""
    kind, value = key
    if kind == "object":
        return "opaque"
    return str(value)[:MAX_SESSION_ID_CHARS]


def _copy_tool_metadata(
    source: dspy.Tool,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None]:
    """Snapshot the source Tool's exact model-facing metadata.

    A Session proxy must not retain the source Tool or its callable, but its
    public contract is frozen: descriptions, JSON schemas, type hints, and
    argument descriptions are copied without redaction, truncation, default
    replacement, or shape changes.  Deep-copying isolates the resident proxy
    from later host mutation while keeping the metadata itself exact.
    """
    try:
        return (
            source.desc,
            copy.deepcopy(source.args),
            copy.deepcopy(source.arg_types),
            copy.deepcopy(source.arg_desc),
        )
    except Exception as exc:
        # A metadata object that cannot be snapshotted cannot safely be
        # installed into a reusable resident program.  Fail closed rather
        # than silently changing the model-facing contract.
        raise ValueError("Tool metadata is not copyable") from exc


def _set_tool_metadata(
    proxy: dspy.Tool,
    metadata: tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None],
) -> None:
    """Apply one exact metadata snapshot to a proxy."""
    description, args, arg_types, arg_desc = metadata
    proxy.desc = description
    proxy.args = args
    proxy.arg_types = arg_types
    proxy.arg_desc = arg_desc


def _call_gate(gate: ClaimCheck | AuthorizationCheck | None, value: str) -> bool:
    """Call a zero- or one-argument gate and fail closed on every defect."""
    if gate is None:
        return False
    try:
        try:
            signature = inspect.signature(gate)
        except (TypeError, ValueError):
            result = gate(value)
        else:
            parameters = tuple(signature.parameters.values())
            accepts_value = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in parameters) or any(
                parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                for parameter in parameters
            )
            result = gate(value) if accepts_value else gate()
        # A synchronous DSPy Tool cannot safely wait on an async claim or
        # authorization callback.  Close a coroutine when possible to avoid a
        # warning, then deny rather than accidentally allowing the call.
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            return False
        return result is True
    except BaseException:
        # Authorization is a fail-closed boundary, including cancellation or
        # an ill-behaved callback supplied by an integration adapter.
        return False


def _authorization_gate(authorization: object | None) -> AuthorizationCheck | None:
    if authorization is None:
        return None
    for name in ("allows", "authorize", "is_authorized"):
        candidate = getattr(authorization, name, None)
        if callable(candidate):
            return cast(AuthorizationCheck, candidate)
    if callable(authorization):
        return cast(AuthorizationCheck, authorization)
    return None


def _claim_gate(authorization: object | None) -> ClaimCheck | None:
    if authorization is None:
        return None
    for name in ("is_live", "is_valid", "valid", "active"):
        candidate = getattr(authorization, name, None)
        if callable(candidate):
            return cast(ClaimCheck, candidate)
    return None


@dataclass(frozen=True, slots=True)
class _ActiveBinding:
    run_key: tuple[str, str | int]
    run_label: str
    tools: Mapping[str, dspy.Tool]
    authorized_names: frozenset[str]
    claim_check: ClaimCheck | None
    authorization_check: AuthorizationCheck | None
    generation: int
    remove_revocation_listener: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionToolBinding:
    """A removable, run-specific lease for an installed Tool set.

    The lease intentionally stores only the registry, a bounded Run label, and
    a generation.  It never stores the claim/authorization object or source
    Tool tuple.  A stale lease cannot clear a newer Turn binding.
    """

    _registry: SessionToolRegistry = field(repr=False, compare=False)
    run_id: str
    generation: int
    tools: tuple[dspy.Tool, ...]
    _run_key: tuple[str, str | int] = field(repr=False, compare=False)

    def remove(self) -> bool:
        """Remove this exact binding, returning false when it is already stale."""
        return self._registry._remove_key(self._run_key, generation=self.generation)

    def clear(self) -> bool:
        """Alias for :meth:`remove`."""
        return self.remove()

    close = remove


class SessionToolRegistry:
    """Own stable proxies and one active Turn capability binding.

    Construct one registry per Session.  ``install`` replaces the complete
    active program Tool set; it does not merge with a prior Turn.  Proxy
    objects for removed names stay as inert aliases so a Python global retained
    by the interpreter fails closed instead of silently acquiring a stale
    callable.
    """

    def __init__(
        self,
        *,
        max_tools: int = MAX_SESSION_TOOL_COUNT,
    ) -> None:
        if not 1 <= max_tools <= MAX_SESSION_TOOL_COUNT:
            raise ValueError("max_tools is invalid")
        self._max_tools = max_tools
        self._lock = RLock()
        self._active: _ActiveBinding | None = None
        self._generation = 0
        self._proxies: dict[str, dspy.Tool] = {}
        self._inflight: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]]]] = {}

    def _new_proxy(self, name: str, source: dspy.Tool | None = None) -> dspy.Tool:
        """Create one proxy whose closure contains only this registry/name."""
        registry = self

        def invoke(**kwargs: Any) -> Any:
            return registry._invoke(name, kwargs)

        if source is None:
            metadata: tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None] = (
                f"Invoke the authorized host capability {name!r}.",
                {},
                {},
                {},
            )
        else:
            metadata = _copy_tool_metadata(source)
        # Keep this constructor spelling aligned with the exact DSPy 3.3.1
        # public Tool signature.  In particular, explicit metadata prevents
        # the proxy's ``**kwargs`` callable from being reflected as its model
        # facing contract.
        proxy = dspy.Tool(
            invoke,
            name=name,
            desc=metadata[0],
            args=metadata[1],
            arg_types=metadata[2],
            arg_desc=metadata[3],
        )
        _set_tool_metadata(proxy, metadata)
        return proxy

    @staticmethod
    def _reset_proxy(proxy: dspy.Tool, name: str) -> None:
        """Keep a stale alias's frozen metadata while making its call inert.

        A retained interpreter global can still inspect a removed Tool. The
        alias must fail closed, but replacing its description/schema would make
        the model-facing contract depend on the last binding lifecycle rather
        than on the frozen source Tool. Metadata is therefore intentionally
        left untouched; only the registry's active binding is cleared.
        """
        del proxy, name

    def _update_proxy(self, proxy: dspy.Tool, source: dspy.Tool, name: str) -> None:
        del name
        _set_tool_metadata(proxy, _copy_tool_metadata(source))

    def _validate_sources(self, tools: Iterable[dspy.Tool]) -> tuple[dspy.Tool, ...]:
        values = tuple(tools)
        if len(values) > self._max_tools:
            raise ValueError("program Tool set is too large")
        names: set[str] = set()
        for tool in values:
            if not isinstance(tool, dspy.Tool):
                raise TypeError("SessionToolRegistry requires dspy.Tool values")
            name = tool.name
            if not isinstance(name, str) or not name or len(name) > MAX_TOOL_NAME_CHARS:
                raise ValueError("Tool name is invalid")
            if name in names:
                raise ValueError("program Tool names must be unique")
            names.add(name)
        return values

    def _cancel_inflight(self, generation: int) -> None:
        """Cancel async proxy calls belonging to a retired binding generation."""
        pending = self._inflight.pop(generation, ())
        for loop, task in pending:
            if task.done():
                continue
            # Removal can be called by a cleanup thread, while the Tool runs
            # on the Session worker loop; marshal cancellation to that loop.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)

    def install(
        self,
        tools: Iterable[dspy.Tool],
        *,
        run_id: object,
        claim_valid: ClaimCheck | None = None,
        authorized_names: Iterable[str] | None = None,
        is_authorized: AuthorizationCheck | None = None,
        authorization: object | None = None,
        revocation: object | None = None,
    ) -> tuple[dspy.Tool, ...]:
        """Replace the active Turn binding and return stable proxy objects.

        ``claim_valid`` and one of ``authorized_names``/``is_authorized`` are
        required for a call to succeed.  Omitting either intentionally installs
        an inert binding, which makes an integration defect fail closed.
        ``authorization`` is a provider-neutral convenience object exposing
        ``is_live`` and ``allows(name)`` (or equivalent method names).
        """
        values = self._validate_sources(tools)
        run_key = _identity_key(run_id, label="run_id")
        run_label = _identity_label(run_key)

        if authorization is not None:
            if claim_valid is None:
                claim_valid = _claim_gate(authorization)
            if is_authorized is None:
                is_authorized = _authorization_gate(authorization)

        names = tuple(cast(str, tool.name) for tool in values)
        if authorized_names is None:
            allowed = frozenset(names) if is_authorized is not None else frozenset()
        else:
            provided = frozenset(name for name in authorized_names if isinstance(name, str))
            allowed = frozenset(name for name in names if name in provided)

        # Snapshot source metadata before mutating the active binding.  This
        # ensures malformed metadata cannot leave a half-installed Tool set.
        metadata: dict[str, tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None]] = {}
        for source, name in zip(values, names, strict=True):
            metadata[name] = _copy_tool_metadata(source)

        with self._lock:
            self._generation += 1
            generation = self._generation
            # Replace, never update, the active mapping.  Dropping this object
            # releases every prior source Tool and its authorization closure.
            active_tools: dict[str, dspy.Tool] = {}
            for source, name in zip(values, names, strict=True):
                proxy = self._proxies.get(name)
                if proxy is None:
                    proxy = self._new_proxy(name)
                    self._proxies[name] = proxy
                _set_tool_metadata(proxy, metadata[name])
                active_tools[name] = source
            for old_name, proxy in self._proxies.items():
                if old_name not in active_tools:
                    self._reset_proxy(proxy, old_name)
            prior = self._active
            if prior is not None:
                self._cancel_inflight(prior.generation)
                if prior.remove_revocation_listener is not None:
                    prior.remove_revocation_listener()
            active = _ActiveBinding(
                run_key=run_key,
                run_label=run_label,
                tools=MappingProxyType(active_tools),
                authorized_names=allowed,
                claim_check=claim_valid,
                authorization_check=is_authorized,
                generation=generation,
            )
            self._active = active
            add_listener = getattr(revocation, "add_revoke_listener", None)
            if callable(add_listener):

                def listener() -> None:
                    self._cancel_inflight(generation)

                try:
                    remove_listener = add_listener(listener)
                except BaseException:
                    remove_listener = None
                self._active = _ActiveBinding(
                    run_key=active.run_key,
                    run_label=active.run_label,
                    tools=active.tools,
                    authorized_names=active.authorized_names,
                    claim_check=active.claim_check,
                    authorization_check=active.authorization_check,
                    generation=active.generation,
                    remove_revocation_listener=remove_listener if callable(remove_listener) else None,
                )
            return tuple(self._proxies[name] for name in names)

    def bind_turn(
        self,
        tools: Iterable[dspy.Tool],
        *,
        run_id: object,
        claim_valid: ClaimCheck | None = None,
        authorized_names: Iterable[str] | None = None,
        is_authorized: AuthorizationCheck | None = None,
        authorization: object | None = None,
        revocation: object | None = None,
    ) -> SessionToolBinding:
        """Install one Turn and return a stale-safe removal lease."""
        proxies = self.install(
            tools,
            run_id=run_id,
            claim_valid=claim_valid,
            authorized_names=authorized_names,
            is_authorized=is_authorized,
            authorization=authorization,
            revocation=revocation,
        )
        with self._lock:
            active = self._active
            if active is None:  # pragma: no cover - install is atomic
                raise RuntimeError("Tool binding was not installed")
            return SessionToolBinding(self, active.run_label, active.generation, proxies, active.run_key)

    # Explicit spelling for callers that prefer the P45 terminology.
    install_turn = bind_turn

    def _remove_key(
        self,
        run_key: tuple[str, str | int] | None,
        *,
        generation: int | None = None,
    ) -> bool:
        """Remove by an already-normalized key (used by stale-safe leases)."""
        with self._lock:
            active = self._active
            if active is None:
                return False
            if run_key is not None and run_key != active.run_key:
                return False
            if generation is not None and generation != active.generation:
                return False
            self._cancel_inflight(active.generation)
            if active.remove_revocation_listener is not None:
                active.remove_revocation_listener()
            self._active = None
            for name, proxy in self._proxies.items():
                self._reset_proxy(proxy, name)
            return True

    def remove(
        self,
        *,
        run_id: object | None = None,
        generation: int | None = None,
    ) -> bool:
        """Remove the active binding if it matches the optional claim fence."""
        run_key = _identity_key(run_id, label="run_id") if run_id is not None else None
        return self._remove_key(run_key, generation=generation)

    def clear(
        self,
        *,
        run_id: object | None = None,
        generation: int | None = None,
    ) -> bool:
        """Alias for :meth:`remove` used by Turn cleanup paths."""
        return self.remove(run_id=run_id, generation=generation)

    def proxy(self, name: str) -> dspy.Tool:
        """Return/create the stable alias for ``name`` without authorizing it."""
        if not isinstance(name, str) or not name or len(name) > MAX_TOOL_NAME_CHARS:
            raise ValueError("Tool name is invalid")
        with self._lock:
            proxy = self._proxies.get(name)
            if proxy is None:
                proxy = self._new_proxy(name)
                self._proxies[name] = proxy
            return proxy

    get_proxy = proxy

    def tools(self) -> tuple[dspy.Tool, ...]:
        """Return exactly the currently installed program Tool set."""
        with self._lock:
            active = self._active
            if active is None:
                return ()
            return tuple(self._proxies[name] for name in active.tools)

    @property
    def active_names(self) -> frozenset[str]:
        """Return names in the current program Tool set, never prior names."""
        with self._lock:
            return frozenset(self._active.tools) if self._active is not None else frozenset()

    @property
    def active_run_id(self) -> str | None:
        """Return a bounded run label for diagnostics, not an auth object."""
        with self._lock:
            return self._active.run_label if self._active is not None else None

    def _authorize(self, active: _ActiveBinding, name: str) -> dspy.Tool:
        """Return the current Tool only after all live authorization checks pass."""
        source = active.tools.get(name)
        if source is None or name not in active.authorized_names:
            raise SessionToolAuthorizationError
        if not _call_gate(active.claim_check, active.run_label):
            raise SessionToolAuthorizationError
        if active.authorization_check is not None and not _call_gate(active.authorization_check, name):
            raise SessionToolAuthorizationError
        return source

    def _invoke(self, name: str, kwargs: Mapping[str, Any]) -> Any:
        """Resolve and invoke one Tool while the binding is current."""
        with self._lock:
            active = self._active
            if active is None:
                raise SessionToolAuthorizationError
            source = self._authorize(active, name)
            result = source.func(**dict(kwargs))
            if not inspect.isawaitable(result):
                # Keep the lock through synchronous source invocation. Same-
                # session Turns cannot swap the source during its side effect.
                return result

        async def await_authorized() -> Any:
            # Register the task as an in-flight generation lease. Retiring a
            # binding cancels it on its owning event loop, so a suspended old
            # Tool cannot resume under a newer Turn's authorization.
            task = asyncio.current_task()
            loop = asyncio.get_running_loop()
            registered = False
            with self._lock:
                current = self._active
                if current is not active:
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise SessionToolAuthorizationError
                self._authorize(current, name)
                if task is not None:
                    self._inflight.setdefault(active.generation, set()).add((loop, task))
                    registered = True
            try:
                value = await result
                # A claim can be revoked without replacing the binding. Check
                # again before publishing the old operation's result.
                with self._lock:
                    current = self._active
                    if current is not active:
                        raise SessionToolAuthorizationError
                    self._authorize(current, name)
                return value
            finally:
                if registered and task is not None:
                    with self._lock:
                        pending = self._inflight.get(active.generation)
                        if pending is not None:
                            pending.discard((loop, task))
                            if not pending:
                                self._inflight.pop(active.generation, None)

        return await_authorized()
