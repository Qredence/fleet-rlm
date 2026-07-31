"""Bounded public-text URL access for one RLM Session.

The Tool keeps URL retrieval behind the host boundary.  The RLM receives the
text as a Python value, while RuntimeEvents and MLflow receive only bounded
source metadata.  Daytona stores successful values in Session Workspace;
Deno uses a bounded process-local Session cache because its documented
Workspace capability is unavailable.
"""

from __future__ import annotations

import hashlib
import ipaddress
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

from fleet_rlm.files.workspace_models import SessionWorkspaceFS, WorkspaceListResult
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text

MAX_URL_REDIRECTS = 3
URL_FETCH_CHUNK_BYTES = 64 * 1024
URL_FETCH_TIMEOUT_SECONDS = 10.0
URL_WORKSPACE_PREFIX = "sources/urls"
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
        """Initialize a categorized tool error with a safe public message.

        Parameters:
            code (str): Identifier for the error category.
            message (str): Message exposed to callers and used as the exception message.
        """
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
        """
        Encode the text content as UTF-8 bytes.

        Returns:
                bytes: The UTF-8 encoded text.
        """
        return self.text.encode("utf-8")


class UrlSourceStore(Protocol):
    """Read and write one Session-scoped normalized URL source."""

    def read(self, session_id: UUID, path: str, *, max_bytes: int) -> str | None:
        """Read cached URL content for a session within the specified byte limit.

        Parameters:
            session_id (UUID): Session whose cached source should be read.
            path (str): Workspace-relative source path.
            max_bytes (int): Maximum number of bytes to read.

        Returns:
            str | None: Cached text, or `None` when the source is unavailable or exceeds the limit.
        """
        ...

    def write(self, session_id: UUID, path: str, content: str, *, max_bytes: int) -> None:
        """Store URL source content for a session within the specified byte limit.

        Parameters:
            session_id (UUID): Session whose source cache receives the content.
            path (str): Workspace-relative path identifying the source.
            content (str): Text content to store.
            max_bytes (int): Maximum UTF-8 encoded size permitted for the content.
        """
        ...


class UrlFetcher(Protocol):
    """Fetch one validated public URL without exposing an HTTP client to Tools."""

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        """Fetch bounded text content from a public HTTPS URL.

        Parameters:
            url (str): HTTPS URL to fetch.
            max_bytes (int): Maximum allowed response size in bytes.

        Returns:
            UrlFetchResult: Normalized fetched content, canonical URL, and media type.

        Raises:
            UrlToolError: If the URL, redirect, response, content type, size, encoding, or network request is invalid.
        """
        ...


def _canonical_url(url: str) -> str:
    """Canonicalize an anonymous public HTTPS URL for fetching.

    Parameters:
        url (str): URL to validate and normalize.

    Returns:
        str: Canonical HTTPS URL with a normalized hostname, explicit path, and no fragment.

    Raises:
        UrlToolError: If the URL is missing, malformed, uses unsupported credentials or a scheme,
            or specifies a port other than 443.
    """
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
    """Generate a stable SHA-256 identifier for a canonical URL.

    Parameters:
        canonical_url (str): The canonical URL to identify.

    Returns:
        str: The hexadecimal SHA-256 digest of the URL.
    """
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def _source_path(source_id: str) -> str:
    """Map a URL source identifier to its workspace-relative text file path.

    Parameters:
        source_id (str): Identifier for the URL source.

    Returns:
        str: Workspace-relative path for the source text file.
    """
    return f"{URL_WORKSPACE_PREFIX}/{source_id}.txt"


def _public_address(host: str) -> str:
    """Resolve a host to a public IP address.

    Parameters:
        host (str): Hostname to resolve.

    Returns:
        str: The lexicographically first resolved IP address.

    Raises:
        UrlToolError: If the host cannot be resolved, resolves to an invalid address,
            or resolves to any non-public address.
    """
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UrlToolError("unreachable", "URL host could not be resolved") from exc
    addresses = {str(info[4][0]) for info in infos if info[4]}
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
    """Extract the normalized media type from a Content-Type header value.

    Parameters:
        content_type (str): The Content-Type header value.

    Returns:
        str: The lowercase media type without parameters.
    """
    return content_type.split(";", 1)[0].strip().lower()


def _charset(content_type: str) -> str:
    """Extract the declared character encoding from a content type.

    Parameters:
        content_type (str): Content type header value to inspect.

    Returns:
        str: Declared character encoding, or ``"utf-8"`` when none is specified.
    """
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
        """
        Open a bounded HTTPS connection for a validated public URL.

        Parameters:
            url (str): The URL to fetch.
            timeout_seconds (float): The total, connection, and read timeout in seconds.

        Returns:
            tuple: The HTTP response and connection pool used for the request.

        Raises:
            UrlToolError: If the URL has no hostname or the request cannot be completed.
        """
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise UrlToolError("invalid_url", "URL hostname is required")
        address = _public_address(host)
        pool = urllib3.HTTPSConnectionPool(
            address,
            port=443,
            timeout=urllib3.Timeout(
                total=timeout_seconds,
                connect=timeout_seconds,
                read=timeout_seconds,
            ),
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
    """Bounded process-local URL cache for Deno's non-durable runtime."""

    def __init__(
        self,
        *,
        max_entries_per_session: int = 8,
        max_entries_total: int = URL_CACHE_MAX_ENTRIES_TOTAL,
        max_bytes_total: int = URL_CACHE_MAX_BYTES_TOTAL,
    ) -> None:
        """Initialize an in-memory URL source store with entry and byte limits.

        Parameters:
                max_entries_per_session (int): Maximum number of cached entries per session.
                max_entries_total (int): Maximum number of cached entries across all sessions.
                max_bytes_total (int): Maximum total UTF-8 cache size in bytes.

        Raises:
                ValueError: If any cache limit is less than 1.
        """
        if max_entries_per_session < 1 or max_entries_total < 1 or max_bytes_total < 1:
            raise ValueError("URL cache bounds must be positive")
        self._max_entries = max_entries_per_session
        self._max_entries_total = max_entries_total
        self._max_bytes_total = max_bytes_total
        self._values: dict[UUID, OrderedDict[str, str]] = {}
        self._order: OrderedDict[tuple[UUID, str], None] = OrderedDict()
        self._total_bytes = 0
        self._lock = RLock()

    def read(self, session_id: UUID, path: str, *, max_bytes: int) -> str | None:
        """Read cached URL source content for a session and path.

        Parameters:
            session_id (UUID): Session whose cached source should be read.
            path (str): Cache path identifying the source.

        Returns:
            str | None: Cached source content, or None if no matching entry exists.
        """
        del max_bytes
        with self._lock:
            values = self._values.get(session_id)
            if values is None or path not in values:
                return None
            value = values.pop(path)
            values[path] = value
            key = (session_id, path)
            self._order.pop(key, None)
            self._order[key] = None
            return value

    def write(self, session_id: UUID, path: str, content: str, *, max_bytes: int) -> None:
        """Store URL content in the session cache, evicting older entries as needed.

        Parameters:
                session_id (UUID): Session whose cache receives the content.
                path (str): Cache path for the URL content.
                content (str): Text content to store.
                max_bytes (int): Maximum UTF-8 size allowed for the content.

        Raises:
                UrlToolError: If the content exceeds the configured size limit.
        """
        if len(content.encode("utf-8")) > max_bytes:
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


class WorkspaceUrlSourceStore:
    """Synchronous adapter over one Daytona Session Workspace filesystem."""

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

    def read(self, session_id: UUID, path: str, *, max_bytes: int) -> str | None:
        """
        Read verified cached URL content from the workspace.

        Parameters:
            session_id (UUID): Session owning the cached content.
            path (str): Workspace path of the cached content.
            max_bytes (int): Maximum allowed UTF-8 content size.

        Returns:
            str | None: Cached text when present and integrity-verified, or None when unavailable or changed.

        Raises:
            UrlToolError: If the content exceeds the size limit or cannot be read completely.
        """
        try:
            entry = self._workspace.stat(path)
        except FileNotFoundError:
            # The Workspace agent correctly reports a missing intermediate
            # directory as FileNotFoundError.  For a deterministic cache path,
            # that is the same cache miss as a missing leaf file.
            return None
        if entry is None:
            return None
        cursor: str | None = None
        chunks: list[str] = []
        total = 0
        while True:
            page = self._workspace.read_text_page(
                path,
                cursor=cursor,
                max_chars=10_000,
                max_bytes=max_bytes,
            )
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
        return "".join(chunks)

    def write(self, session_id: UUID, path: str, content: str, *, max_bytes: int) -> None:
        """
        Store URL content in the session workspace when configured cache limits allow it.

        Parameters:
            session_id (UUID): Session associated with the cached content.
            path (str): Workspace path used to store the content.
            content (str): Text content to cache.
            max_bytes (int): Maximum UTF-8 encoded size permitted for this content.

        Raises:
            UrlToolError: If the content exceeds `max_bytes` or the workspace is unavailable.
        """
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > max_bytes:
            raise UrlToolError("too_large", "URL content exceeds the configured size limit")
        try:
            try:
                listing = self._workspace.list_entries(
                    URL_WORKSPACE_PREFIX,
                    limit=min(100, self._max_entries_total + 1),
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
                return
            total_bytes = sum(entry.byte_size or 0 for entry in cache_entries)
            if existing is None and len(cache_entries) >= self._max_entries_total:
                return
            existing_bytes = 0
            if existing is not None:
                if existing.byte_size is None:
                    return
                existing_bytes = existing.byte_size
            if total_bytes - existing_bytes + content_bytes > self._max_bytes_total:
                return
            self._workspace.write_text(path, content, overwrite=True)
            cache_key = (session_id, path)
            with _WORKSPACE_CACHE_INTEGRITY_LOCK:
                _WORKSPACE_CACHE_INTEGRITY[cache_key] = hashlib.sha256(content.encode("utf-8")).hexdigest()
                _WORKSPACE_CACHE_INTEGRITY.move_to_end(cache_key)
                while len(_WORKSPACE_CACHE_INTEGRITY) > URL_CACHE_MAX_ENTRIES_TOTAL:
                    _WORKSPACE_CACHE_INTEGRITY.popitem(last=False)
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
        """
        Initialize a URL tool host for a session.

        Parameters:
            session_id (UUID): Session identifier used for source storage.
            store (UrlSourceStore): Session-scoped store for cached URL content.
            max_bytes (int): Maximum number of UTF-8 bytes allowed for fetched content.
            fetcher (UrlFetcher | None): Fetcher to use, or a default public HTTPS fetcher.

        Raises:
            ValueError: If max_bytes is less than 1.
        """
        if max_bytes < 1:
            raise ValueError("URL Tool byte bound must be positive")
        self._session_id = session_id
        self._store = store
        self._max_bytes = max_bytes
        self._fetcher = fetcher or UrllibPublicTextFetcher()

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """
        Create the session-bound public HTTPS text retrieval tool.

        Returns:
            tuple[dspy.Tool, ...]: A tuple containing the configured ``fetch_url`` tool.
        """

        def fetch_url(url: str) -> dict[str, object]:
            """Fetch one relevant public HTTPS text URL into a Python value and Session cache."""
            try:
                canonical = _canonical_url(url)
                source_id = _source_id(canonical)
                path = _source_path(source_id)
                cached = self._store.read(self._session_id, path, max_bytes=self._max_bytes)
                if cached is not None:
                    # The store keeps only the normalized text; the origin
                    # content type is unknown here, so omit it rather than
                    # fabricate one that may differ from the first fetch.
                    return self._result(
                        source_id=source_id,
                        canonical_url=canonical,
                        path=path,
                        content_type=None,
                        text=cached,
                        cache_hit=True,
                    )
                fetched = self._fetcher.fetch(canonical, max_bytes=self._max_bytes)
                self._store.write(self._session_id, path, fetched.text, max_bytes=self._max_bytes)
                return self._result(
                    source_id=source_id,
                    canonical_url=fetched.canonical_url,
                    path=path,
                    content_type=fetched.content_type,
                    text=fetched.text,
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
        """
        Expose redacted event projections for the URL-fetching tool.

        Returns:
            Mapping[str, ToolEventView]: The ``fetch_url`` event view, exposing only
            canonical source identifiers and bounded result metadata.
        """

        def input_projection(arguments: Mapping[str, object]) -> JsonValue:
            raw_url = arguments.get("url")
            try:
                canonical = _canonical_url(str(raw_url))
                return {"source_id": _source_id(canonical)}
            except UrlToolError:
                return {}

        def output_projection(result: object) -> JsonValue:
            """Create a bounded event-safe projection of a tool result."""
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
        path: str,
        content_type: str | None,
        text: str,
        cache_hit: bool,
    ) -> dict[str, object]:
        """
        Build a successful URL retrieval result with content and source metadata.

        Parameters:
            source_id (str): Identifier for the retrieved source.
            canonical_url (str): Normalized URL associated with the source.
            path (str): Workspace path used to store the source.
            content_type (str | None): Retrieved media type, when available.
            text (str): Retrieved text content.
            cache_hit (bool): Whether the content came from the cache.

        Returns:
            dict[str, object]: Result containing the content, source metadata, UTF-8 byte size, checksum,
                and cache status.
        """
        data = text.encode("utf-8")
        result: dict[str, object] = {
            "ok": True,
            "source_id": source_id,
            "canonical_url": canonical_url,
            "workspace_path": path,
            "content": text,
            "byte_size": len(data),
            "checksum_sha256": hashlib.sha256(data).hexdigest(),
            "cache_hit": cache_hit,
        }
        if content_type is not None:
            result["content_type"] = content_type
        return result


__all__ = [
    "InMemoryUrlSourceStore",
    "UrlFetchResult",
    "UrlFetcher",
    "UrlSourceStore",
    "UrlToolError",
    "UrlToolHost",
    "UrllibPublicTextFetcher",
    "WorkspaceUrlSourceStore",
]
