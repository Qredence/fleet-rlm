"""Bounded public-text URL access for one RLM Session.

The Tool keeps URL retrieval behind the host boundary.  The RLM receives the
text as a Python value, while RuntimeEvents and MLflow receive only bounded
source metadata. Daytona stores successful values in Session Workspace; private
or local execution uses a bounded process-local Session cache when durable
Workspace capability is unavailable.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Protocol, cast
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

import dspy
import urllib3

from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import ToolEventView, bound_event_text
from fleet_rlm.workspace.models import SessionWorkspaceFS, WorkspaceListResult

MAX_URL_REDIRECTS = 3
URL_FETCH_CHUNK_BYTES = 64 * 1024
URL_FETCH_TIMEOUT_SECONDS = 10.0
URL_WORKSPACE_PREFIX = "sources/urls"
URL_INLINE_CONTENT_MAX_BYTES = 1 * 1024 * 1024
URL_CONTENT_PREVIEW_CHARS = 4_000
URL_CACHE_MAX_ENTRIES_TOTAL = 64
URL_CACHE_MAX_BYTES_TOTAL = 64 * 1024 * 1024
_CHARSET_RE = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"']+)", re.IGNORECASE)
_ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "text/html",
        "text/plain",
        "text/xml",
    }
)
_WORKSPACE_CACHE_INTEGRITY: OrderedDict[tuple[UUID, str], str] = OrderedDict()
_WORKSPACE_CACHE_INTEGRITY_LOCK = RLock()


class UrlToolError(RuntimeError):
    """Safe failure returned to generated URL-tool callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True, slots=True)
class UrlFetchResult:
    """Normalized text returned by the injected network adapter."""

    canonical_url: str
    content_type: str
    text: str

    @property
    def data(self) -> bytes:
        return self.text.encode("utf-8")


@dataclass(frozen=True, slots=True)
class StoredUrlSource:
    """One fetched source plus the truthfulness of its returned reference."""

    text: str
    durable: bool
    workspace_path: str | None
    byte_size: int
    checksum_sha256: str


class UrlSourceStore(Protocol):
    """Read and write one Session-scoped normalized URL source."""

    def read(self, session_id: UUID, path: str, *, max_bytes: int) -> StoredUrlSource | None:
        raise NotImplementedError

    def write(self, session_id: UUID, path: str, content: str, *, max_bytes: int) -> StoredUrlSource:
        raise NotImplementedError


class UrlFetcher(Protocol):
    """Fetch one validated public URL without exposing an HTTP client to Tools."""

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult: ...


def _canonical_url(url: str) -> str:
    """Canonicalize an anonymous public HTTPS URL for fetching."""
    if not isinstance(url, str) or not url.strip():
        raise UrlToolError("invalid_url", "URL is required")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise UrlToolError("invalid_url", "URL is invalid") from exc
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        raise UrlToolError("invalid_url", "Only anonymous public HTTPS URLs are supported")
    host = parsed.hostname
    if not host:
        raise UrlToolError("invalid_url", "URL hostname is required")
    if port not in (None, 443):
        raise UrlToolError("invalid_url", "Only HTTPS port 443 is supported")
    try:
        host = host.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError as exc:
        raise UrlToolError("invalid_url", "URL hostname is invalid") from exc
    netloc = f"[{host}]" if ":" in host else host
    path = parsed.path or "/"
    return urlunsplit(("https", netloc, path, parsed.query, ""))


def _source_id(canonical_url: str) -> str:
    """Generate a stable SHA-256 identifier for a canonical URL."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def _source_path(source_id: str) -> str:
    return f"{URL_WORKSPACE_PREFIX}/{source_id}.txt"


def _public_address(host: str) -> str:
    """Resolve a host to a public IP address."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UrlToolError("unreachable", "URL host could not be resolved") from exc
    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        raise UrlToolError("unreachable", "URL host could not be resolved")
    try:
        parsed = tuple(ipaddress.ip_address(address) for address in addresses)
    except ValueError as exc:
        raise UrlToolError("unreachable", "URL host resolved to an invalid address") from exc
    if any(not address.is_global for address in parsed):
        raise UrlToolError("forbidden_host", "URL host must resolve only to public addresses")
    return sorted(addresses)[0]


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _charset(content_type: str) -> str:
    match = _CHARSET_RE.search(content_type)
    return match.group(1).strip() if match else "utf-8"


class UrllibPublicTextFetcher:
    """Synchronous public-text fetcher for DSPy's synchronous Tool bridge."""

    def __init__(
        self,
        *,
        timeout_seconds: float = URL_FETCH_TIMEOUT_SECONDS,
        max_redirects: int = MAX_URL_REDIRECTS,
    ) -> None:
        """Initialize a public URL fetcher with timeout and redirect limits.

        Parameters:
                timeout_seconds (float): Maximum duration allowed for a fetch.
                max_redirects (int): Maximum number of redirects permitted.
        """
        if timeout_seconds <= 0 or max_redirects < 0:
            raise ValueError("URL fetch limits must be positive")
        self._timeout_seconds = float(timeout_seconds)
        self._max_redirects = max_redirects

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        """
        Fetch bounded text content from a public HTTPS URL.

        Parameters:
                url (str): The URL to fetch.
                max_bytes (int): Maximum permitted size of the response and its normalized UTF-8 text.

        Returns:
                UrlFetchResult: The canonical URL, content type, and decoded text.

        Raises:
                ValueError: If max_bytes is less than 1.
                UrlToolError: If the URL, redirect chain, response, content, encoding, or fetch duration
                    violates the configured limits or requirements.
        """
        if max_bytes < 1:
            raise ValueError("URL fetch byte bound must be positive")
        current = _canonical_url(url)
        deadline = time.monotonic() + self._timeout_seconds
        for redirect_count in range(self._max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UrlToolError("timeout", "URL fetch exceeded the configured time limit")
            response, pool = self._open(current, timeout_seconds=remaining)
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise UrlToolError("redirect_invalid", "URL redirect has no destination")
                    if redirect_count >= self._max_redirects:
                        raise UrlToolError("redirect_limit", "URL redirect limit exceeded")
                    current = _canonical_url(urljoin(current, location))
                    continue
                if response.status < 200 or response.status >= 300:
                    raise UrlToolError("http_error", "URL returned an unsuccessful response")
                content_type = response.headers.get("Content-Type", "").strip()
                media_type = _media_type(content_type)
                if media_type and media_type not in _ALLOWED_MEDIA_TYPES and not media_type.startswith("text/"):
                    raise UrlToolError("unsupported_content", "URL content is not supported text")
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > max_bytes:
                            raise UrlToolError("too_large", "URL content exceeds the configured size limit")
                    except ValueError as exc:
                        raise UrlToolError("http_error", "URL response length is invalid") from exc
                data = bytearray()
                try:
                    chunks = response.stream(URL_FETCH_CHUNK_BYTES, decode_content=True)
                    for chunk in chunks:
                        if time.monotonic() >= deadline:
                            raise UrlToolError("timeout", "URL fetch exceeded the configured time limit")
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise UrlToolError("too_large", "URL content exceeds the configured size limit")
                except urllib3.exceptions.ReadTimeoutError as exc:
                    raise UrlToolError("timeout", "URL fetch exceeded the configured time limit") from exc
                if time.monotonic() >= deadline:
                    raise UrlToolError("timeout", "URL fetch exceeded the configured time limit")
                try:
                    text = bytes(data).decode(_charset(content_type))
                except (LookupError, UnicodeDecodeError) as exc:
                    raise UrlToolError("invalid_text", "URL content is not valid text") from exc
                if "\x00" in text:
                    raise UrlToolError("invalid_text", "URL content is not valid text")
                encoded = text.encode("utf-8")
                if len(encoded) > max_bytes:
                    raise UrlToolError("too_large", "Normalized URL content exceeds the configured size limit")
                return UrlFetchResult(current, content_type or "text/plain; charset=utf-8", text)
            finally:
                try:
                    response.release_conn()
                finally:
                    try:
                        response.close()
                    finally:
                        pool.close()
        raise UrlToolError("redirect_limit", "URL redirect limit exceeded")

    def _open(
        self,
        url: str,
        *,
        timeout_seconds: float,
    ) -> tuple[urllib3.response.BaseHTTPResponse, urllib3.HTTPSConnectionPool]:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise UrlToolError("invalid_url", "URL hostname is required")
        address = _public_address(host)
        pool = urllib3.HTTPSConnectionPool(
            address,
            port=443,
            timeout=urllib3.Timeout(total=timeout_seconds, connect=timeout_seconds, read=timeout_seconds),
            maxsize=1,
            cert_reqs="CERT_REQUIRED",
            assert_hostname=host,
            server_hostname=host,
        )
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            response = pool.urlopen(
                "GET",
                target,
                headers={
                    "Accept": "text/*, application/json, application/xml",
                    "Host": host,
                    "User-Agent": "fleet-rlm/1",
                },
                assert_same_host=False,
                preload_content=False,
                decode_content=True,
                redirect=False,
                retries=False,
            )
            return response, pool
        except urllib3.exceptions.HTTPError as exc:
            pool.close()
            raise UrlToolError("unreachable", "URL could not be fetched") from exc
        except Exception:
            pool.close()
            raise


class InMemoryUrlSourceStore:
    """Bounded process-local URL cache for runtimes without durable Workspace."""

    def __init__(
        self,
        *,
        max_entries_per_session: int = 8,
        max_entries_total: int = URL_CACHE_MAX_ENTRIES_TOTAL,
        max_bytes_total: int = URL_CACHE_MAX_BYTES_TOTAL,
    ) -> None:
        if max_entries_per_session < 1 or max_entries_total < 1 or max_bytes_total < 1:
            raise ValueError("URL cache bounds must be positive")
        self._max_entries = max_entries_per_session
        self._max_entries_total = max_entries_total
        self._max_bytes_total = max_bytes_total
        self._values: dict[UUID, OrderedDict[str, str]] = {}
        self._order: OrderedDict[tuple[UUID, str], None] = OrderedDict()
        self._total_bytes = 0
        self._lock = RLock()

    def read(self, session_id: UUID, path: str, *, max_bytes: int) -> StoredUrlSource | None:
        with self._lock:
            values = self._values.get(session_id)
            if values is None or path not in values:
                return None
            value = values.pop(path)
            values[path] = value
            key = (session_id, path)
            self._order.pop(key, None)
            self._order[key] = None
            data = value.encode("utf-8")
            if len(data) > max_bytes:
                raise UrlToolError("too_large", "Cached URL content exceeds the configured size limit")
            return StoredUrlSource(
                text=value,
                durable=False,
                workspace_path=None,
                byte_size=len(data),
                checksum_sha256=hashlib.sha256(data).hexdigest(),
            )

    def write(self, session_id: UUID, path: str, content: str, *, max_bytes: int) -> StoredUrlSource:
        data = content.encode("utf-8")
        if len(data) > max_bytes:
            raise UrlToolError("too_large", "URL content exceeds the configured size limit")
        with self._lock:
            values = self._values.setdefault(session_id, OrderedDict())
            previous = values.pop(path, None)
            key = (session_id, path)
            self._order.pop(key, None)
            if previous is not None:
                self._total_bytes -= len(previous.encode("utf-8"))
            values[path] = content
            self._order[key] = None
            self._total_bytes += len(content.encode("utf-8"))
            while len(values) > self._max_entries:
                evicted_path, evicted_content = values.popitem(last=False)
                self._order.pop((session_id, evicted_path), None)
                self._total_bytes -= len(evicted_content.encode("utf-8"))
            while self._order and (
                len(self._order) > self._max_entries_total or self._total_bytes > self._max_bytes_total
            ):
                evicted_session_id, evicted_path = self._order.popitem(last=False)[0]
                evicted_values = self._values.get(evicted_session_id)
                if evicted_values is None:
                    continue
                evicted_content = evicted_values.pop(evicted_path, None)
                if evicted_content is not None:
                    self._total_bytes -= len(evicted_content.encode("utf-8"))
                if not evicted_values:
                    self._values.pop(evicted_session_id, None)
            if path not in values:
                raise UrlToolError("cache_full", "URL source cache is full")
            return StoredUrlSource(
                text=content,
                durable=False,
                workspace_path=None,
                byte_size=len(data),
                checksum_sha256=hashlib.sha256(data).hexdigest(),
            )


class WorkspaceUrlSourceStore:
    """Synchronous adapter over one Session Workspace filesystem."""

    def __init__(
        self,
        workspace: SessionWorkspaceFS,
        *,
        max_entries_total: int = URL_CACHE_MAX_ENTRIES_TOTAL,
        max_bytes_total: int = URL_CACHE_MAX_BYTES_TOTAL,
    ) -> None:
        """Initialize a workspace-backed URL source store with global cache limits.

        Parameters:
            workspace (SessionWorkspaceFS): Workspace filesystem used to store URL sources.
            max_entries_total (int): Maximum number of cached URL sources.
            max_bytes_total (int): Maximum total UTF-8 bytes stored across URL sources.

        Raises:
            ValueError: If either cache limit is less than one.
        """
        if max_entries_total < 1 or max_bytes_total < 1:
            raise ValueError("URL cache bounds must be positive")
        self._workspace = workspace
        self._max_entries_total = max_entries_total
        self._max_bytes_total = max_bytes_total

    def read(self, session_id: UUID, path: str, *, max_bytes: int) -> StoredUrlSource | None:
        try:
            entry = self._workspace.stat(path)
        except FileNotFoundError:
            return None
        if entry is None:
            return None
        cursor: str | None = None
        chunks: list[str] = []
        total = 0
        while True:
            page = self._workspace.read_text_page(path, cursor=cursor, max_chars=10_000, max_bytes=max_bytes)
            chunks.append(page.content)
            total += len(page.content.encode("utf-8"))
            if total > max_bytes:
                raise UrlToolError("too_large", "Cached URL content exceeds the configured size limit")
            if page.eof:
                break
            if page.next_cursor is None:
                raise UrlToolError("cache_unavailable", "Cached URL content could not be read")
            cursor = page.next_cursor
        checksum = hashlib.sha256("".join(chunks).encode("utf-8")).hexdigest()
        cache_key = (session_id, path)
        with _WORKSPACE_CACHE_INTEGRITY_LOCK:
            expected = _WORKSPACE_CACHE_INTEGRITY.get(cache_key)
            if expected is None or expected != checksum:
                _WORKSPACE_CACHE_INTEGRITY.pop(cache_key, None)
                return None
            _WORKSPACE_CACHE_INTEGRITY.move_to_end(cache_key)
        text = "".join(chunks)
        data = text.encode("utf-8")
        return StoredUrlSource(
            text=text,
            durable=True,
            workspace_path=path,
            byte_size=len(data),
            checksum_sha256=checksum,
        )

    def write(self, session_id: UUID, path: str, content: str, *, max_bytes: int) -> StoredUrlSource:
        data = content.encode("utf-8")
        content_bytes = len(data)
        if content_bytes > max_bytes:
            raise UrlToolError("too_large", "URL content exceeds the configured size limit")
        try:
            try:
                listing = self._workspace.list_entries(
                    URL_WORKSPACE_PREFIX, limit=min(100, self._max_entries_total + 1)
                )
            except FileNotFoundError:
                listing = WorkspaceListResult(())
            cache_entries = tuple(
                entry
                for entry in listing.entries
                if entry.kind == "file" and entry.path.startswith(f"{URL_WORKSPACE_PREFIX}/")
            )
            existing = next((entry for entry in cache_entries if entry.path == path), None)
            if listing.truncated or any(entry.byte_size is None for entry in cache_entries):
                raise UrlToolError("cache_unavailable", "URL source cache cannot be verified")
            total_bytes = sum(entry.byte_size or 0 for entry in cache_entries)
            if existing is None and len(cache_entries) >= self._max_entries_total:
                raise UrlToolError("cache_full", "URL source cache is full")
            existing_bytes = 0
            if existing is not None:
                if existing.byte_size is None:
                    raise UrlToolError("cache_unavailable", "URL source cache cannot be verified")
                existing_bytes = existing.byte_size
            if total_bytes - existing_bytes + content_bytes > self._max_bytes_total:
                raise UrlToolError("cache_full", "URL source cache is full")
            entry = self._workspace.write_text(path, content, overwrite=True)
            if entry.path != path or entry.kind != "file" or entry.byte_size != content_bytes:
                raise UrlToolError("cache_unavailable", "URL source cache write could not be verified")
            checksum = hashlib.sha256(data).hexdigest()
            cache_key = (session_id, path)
            with _WORKSPACE_CACHE_INTEGRITY_LOCK:
                _WORKSPACE_CACHE_INTEGRITY[cache_key] = checksum
                _WORKSPACE_CACHE_INTEGRITY.move_to_end(cache_key)
                while len(_WORKSPACE_CACHE_INTEGRITY) > URL_CACHE_MAX_ENTRIES_TOTAL:
                    _WORKSPACE_CACHE_INTEGRITY.popitem(last=False)
            return StoredUrlSource(
                text=content,
                durable=True,
                workspace_path=path,
                byte_size=content_bytes,
                checksum_sha256=checksum,
            )
        except UrlToolError:
            raise
        except Exception as exc:
            raise UrlToolError("cache_unavailable", "Session Workspace is unavailable") from exc


class UrlToolHost:
    """Bind one authorized Session URL Tool to a cache and fetch adapter."""

    def __init__(
        self,
        *,
        session_id: UUID,
        store: UrlSourceStore,
        max_bytes: int,
        fetcher: UrlFetcher | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("URL Tool byte bound must be positive")
        self._session_id = session_id
        self._store = store
        self._max_bytes = max_bytes
        self._fetcher = fetcher or UrllibPublicTextFetcher()

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def fetch_url(url: str) -> dict[str, object]:
            """Fetch one relevant public HTTPS text URL into a Python value and Session cache."""
            try:
                canonical = _canonical_url(url)
                source_id = _source_id(canonical)
                path = _source_path(source_id)
                cached = self._store.read(self._session_id, path, max_bytes=self._max_bytes)
                if cached is not None:
                    return self._result(
                        source_id=source_id,
                        canonical_url=canonical,
                        content_type=None,
                        source=cached,
                        cache_hit=True,
                    )
                fetched = self._fetcher.fetch(canonical, max_bytes=self._max_bytes)
                source = self._store.write(self._session_id, path, fetched.text, max_bytes=self._max_bytes)
                return self._result(
                    source_id=source_id,
                    canonical_url=fetched.canonical_url,
                    content_type=fetched.content_type,
                    source=source,
                    cache_hit=False,
                )
            except UrlToolError as exc:
                return {"ok": False, "error": exc.code, "message": exc.public_message}
            except Exception:
                return {"ok": False, "error": "unavailable", "message": "URL source is unavailable"}

        return (
            dspy.Tool(
                fetch_url,
                name="fetch_url",
                desc=(
                    "Fetch one relevant anonymous public HTTPS text URL. The result is a Python value; "
                    "do not print the complete content. Repeated calls reuse the Session cache."
                ),
                args={"url": {"type": "string"}},
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def input_projection(arguments: Mapping[str, object]) -> JsonValue:
            raw_url = arguments.get("url")
            try:
                canonical = _canonical_url(str(raw_url))
                return {"source_id": _source_id(canonical)}
            except UrlToolError:
                return {}

        def output_projection(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            allowed = (
                "ok",
                "error",
                "source_id",
                "cache_hit",
                "content_type",
                "byte_size",
                "checksum_sha256",
            )
            values = cast(Mapping[str, object], result)
            return {
                name: bound_event_text(values[name]) if isinstance(values[name], str) else cast(JsonValue, values[name])
                for name in allowed
                if name in values
            }

        return {"fetch_url": ToolEventView(input_projection=input_projection, output_projection=output_projection)}

    @staticmethod
    def _result(
        *,
        source_id: str,
        canonical_url: str,
        content_type: str | None,
        source: StoredUrlSource,
        cache_hit: bool,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": True,
            "source_id": source_id,
            "canonical_url": canonical_url,
            "byte_size": source.byte_size,
            "checksum_sha256": source.checksum_sha256,
            "cache_hit": cache_hit,
        }
        if source.durable:
            assert source.workspace_path is not None
            result["workspace_path"] = source.workspace_path
        inline = {**result, "content": source.text}
        inline_bytes = json.dumps(inline, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(inline_bytes) <= URL_INLINE_CONTENT_MAX_BYTES:
            result["content"] = source.text
        elif source.durable:
            result["content_available"] = True
            result["content_preview"] = source.text[:URL_CONTENT_PREVIEW_CHARS]
        else:
            raise UrlToolError("cache_unavailable", "Large URL content requires Session Workspace storage")
        if content_type is not None:
            result["content_type"] = content_type
        return result


__all__ = [
    "URL_INLINE_CONTENT_MAX_BYTES",
    "InMemoryUrlSourceStore",
    "StoredUrlSource",
    "UrlFetchResult",
    "UrlFetcher",
    "UrlSourceStore",
    "UrlToolError",
    "UrlToolHost",
    "UrllibPublicTextFetcher",
    "WorkspaceUrlSourceStore",
]
