"""URL-backed document source helpers for ReAct document ingestion."""

from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/x-yaml",
    "application/yaml",
)

_URL_SUFFIX_BY_CONTENT_TYPE = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "application/json": ".json",
    "text/plain": ".txt",
    "text/markdown": ".md",
}


def _env_bool(name: str, default: bool) -> bool:
    from fleet_rlm.infrastructure.config._env_utils import env_bool

    return env_bool(os.getenv(name), default=default)


def _env_int(name: str, default: int) -> int:
    from fleet_rlm.infrastructure.config._env_utils import env_int

    return env_int(os.getenv(name), default=default)


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalize_content_type(raw_content_type: str | None) -> str:
    if not raw_content_type:
        return ""
    return raw_content_type.split(";", 1)[0].strip().lower()


def _rewrite_gist_page_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "gist.github.com":
        return url

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return url

    user, gist_id = parts[0], parts[1]
    if not user or not gist_id:
        return url

    return f"https://gist.githubusercontent.com/{user}/{gist_id}/raw"


def _resolve_host_ips(
    hostname: str, port: int
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    results: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        ip_obj = ipaddress.ip_address(ip_str)
        if isinstance(ip_obj, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            results.add(ip_obj)
    return results


def _assert_public_http_url(url: str, *, allow_private_net: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// URLs are supported."
        )
    if not parsed.hostname:
        raise ValueError(f"Invalid URL (missing hostname): {url}")

    hostname = parsed.hostname
    if hostname in {"localhost", "localhost.localdomain"} and not allow_private_net:
        raise ValueError(f"Blocked local/private URL target: {url}")

    if allow_private_net:
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved_ips = _resolve_host_ips(hostname, port)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname for URL '{url}': {exc}") from exc

    for ip in resolved_ips:
        if not ip.is_global:
            raise ValueError(f"Blocked local/private URL target: {url} ({ip})")


def _infer_download_suffix(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    path_suffix = Path(parsed.path).suffix.lower()
    if path_suffix:
        return path_suffix
    return _URL_SUFFIX_BY_CONTENT_TYPE.get(content_type, ".bin")


def _decode_text_body(body: bytes, response: httpx.Response) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _read_stream_bytes_limited(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"Fetched URL content exceeds size limit ({max_bytes} bytes)."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url_document_content(
    url: str,
    *,
    read_document_content: Callable[[Path], tuple[str, dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    """Fetch and extract a document from a public HTTP(S) URL."""
    if not _env_bool("FLEET_URL_DOCUMENT_FETCH_ENABLED", True):
        raise ValueError(
            "URL document loading is disabled. Set FLEET_URL_DOCUMENT_FETCH_ENABLED=true to enable it."
        )

    max_bytes = _env_int("FLEET_URL_DOCUMENT_MAX_BYTES", 10 * 1024 * 1024)
    max_redirects = _env_int("FLEET_URL_DOCUMENT_MAX_REDIRECTS", 5)
    connect_timeout = _env_int("FLEET_URL_DOCUMENT_CONNECT_TIMEOUT_SECONDS", 10)
    read_timeout = _env_int("FLEET_URL_DOCUMENT_TIMEOUT_SECONDS", 20)
    allow_private_net = _env_bool("FLEET_URL_DOCUMENT_ALLOW_PRIVATE_NET", False)
    user_agent = os.getenv(
        "FLEET_URL_DOCUMENT_USER_AGENT", "fleet-rlm/url-document-loader"
    )

    current_url = _rewrite_gist_page_url(url)
    current_redirects = 0
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=read_timeout,
    )

    with httpx.Client(timeout=timeout, headers={"User-Agent": user_agent}) as client:
        while True:
            _assert_public_http_url(current_url, allow_private_net=allow_private_net)
            try:
                with client.stream(
                    "GET",
                    current_url,
                    follow_redirects=False,
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError(
                                f"URL redirect from '{current_url}' missing Location header."
                            )
                        if current_redirects >= max_redirects:
                            raise ValueError(
                                f"Too many redirects while fetching URL '{url}'."
                            )
                        current_url = str(response.url.join(location))
                        current_redirects += 1
                        continue

                    if response.status_code >= 400:
                        raise ValueError(
                            f"Failed to fetch URL '{url}' (HTTP {response.status_code})."
                        )

                    final_url = str(response.url)
                    _assert_public_http_url(
                        final_url, allow_private_net=allow_private_net
                    )

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = None
                        if declared_length is not None and declared_length > max_bytes:
                            raise ValueError(
                                f"Fetched URL content exceeds size limit ({max_bytes} bytes)."
                            )

                    body = _read_stream_bytes_limited(response, max_bytes=max_bytes)
                    content_type = _normalize_content_type(
                        response.headers.get("content-type")
                    )

                    base_meta: dict[str, Any] = {
                        "source_kind": "url",
                        "source_url": url,
                        "final_url": final_url,
                        "http_status": response.status_code,
                        "content_type": content_type or "application/octet-stream",
                        "fetched_bytes": len(body),
                        "fetch_method": "httpx",
                    }

                    if (
                        content_type.startswith("text/html")
                        or content_type == "application/xhtml+xml"
                    ):
                        suffix = _infer_download_suffix(final_url, content_type)
                        with tempfile.NamedTemporaryFile(
                            suffix=suffix, delete=True
                        ) as tmp:
                            tmp_path = Path(tmp.name)
                            tmp.write(body)
                            tmp.flush()
                            text, extracted_meta = read_document_content(tmp_path)
                        return text, {**base_meta, **extracted_meta}

                    if content_type.startswith(_TEXTUAL_CONTENT_TYPES):
                        return _decode_text_body(body, response), {
                            **base_meta,
                            "source_type": "text",
                            "extraction_method": "httpx_text",
                        }

                    suffix = _infer_download_suffix(final_url, content_type)
                    if suffix in {
                        ".pdf",
                        ".doc",
                        ".docx",
                        ".ppt",
                        ".pptx",
                        ".xls",
                        ".xlsx",
                        ".rtf",
                        ".epub",
                        ".html",
                        ".htm",
                    }:
                        with tempfile.NamedTemporaryFile(
                            suffix=suffix, delete=True
                        ) as tmp:
                            tmp_path = Path(tmp.name)
                            tmp.write(body)
                            tmp.flush()
                            text, extracted_meta = read_document_content(tmp_path)
                        return text, {**base_meta, **extracted_meta}

                    raise ValueError(
                        f"Fetched URL appears to be unsupported/binary content (content-type: {content_type or 'unknown'})."
                    )
            except httpx.TimeoutException as exc:
                raise ValueError(f"Timed out fetching URL '{url}'.") from exc
            except httpx.HTTPError as exc:
                raise ValueError(f"Failed to fetch URL '{url}': {exc}") from exc
