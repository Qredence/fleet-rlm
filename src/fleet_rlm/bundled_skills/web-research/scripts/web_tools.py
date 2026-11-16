"""Dual-mode web search, bounded page extraction, and JSON retrieval helpers."""

from __future__ import annotations

import argparse
import contextlib
import html
import importlib
import ipaddress
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from html.parser import HTMLParser
from typing import Any

_USER_AGENT = "fleet-rlm-web-research/1.0 (+https://github.com/qredence/fleet-rlm)"
_VALID_PROVIDERS = frozenset({"auto", "ddg", "wikipedia", "arxiv", "github", "pypi"})
_HOST_SEARCH_MAX_RESULTS = 10
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "svg", "nav", "footer"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "main", "li", "tr", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}
)
_HEADING_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}


def _try_import(module_name: str) -> Any | None:
    """Dynamically import an optional snapshot package without failing static checks."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _validate_http_url(url: str) -> str:
    """Validate that a URL uses http or https with a non-empty host."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    cleaned = url.strip()
    parts = urllib.parse.urlsplit(cleaned)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"Unsupported or invalid URL (expected http/https): {url!r}")
    return cleaned


def _merge_params(url: str, params: dict[str, Any] | None) -> str:
    """Append query parameters cleanly to an http/https URL."""
    if not params:
        return url
    parts = urllib.parse.urlsplit(url)
    existing = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    extra = [(str(k), str(v)) for k, v in params.items() if v is not None]
    query = urllib.parse.urlencode([*existing, *extra])
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _charset_from_content_type(content_type: str) -> str:
    """Extract charset from a Content-Type header, defaulting to utf-8."""
    match = re.search(r"charset=([^\s;\"']+)", content_type, re.IGNORECASE)
    return match.group(1).strip() if match else "utf-8"


def _require_public_destination(url: str) -> None:
    """Reject URLs whose host resolves to any address that is not globally routable.

    Loopback, private, link-local, reserved, multicast, unspecified, and shared (CGNAT)
    ranges are refused. Every resolved address must pass, so a mixed DNS answer is rejected.
    """
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host {host!r}: {exc}") from exc
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]))
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if address.is_multicast or not address.is_global:
            raise ValueError(f"Refusing to fetch {host!r}: resolves to non-public address {address}")


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every redirect hop before urllib follows it."""

    def __init__(self, *, public_only: bool) -> None:
        super().__init__()
        self._public_only = public_only

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Refuse non-http(s) hops and, for public-only fetches, non-public destinations."""
        _validate_http_url(newurl)
        if self._public_only:
            _require_public_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_request(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    max_bytes: int = 2 * 1024 * 1024,
    timeout: int = 20,
    raise_on_exceed: bool = False,
    public_only: bool = False,
) -> tuple[int, str, str, bytes, bool]:
    """Perform a bounded stdlib urllib request; each redirect hop is validated before it is followed."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    target_url = _merge_params(_validate_http_url(url), params)
    if public_only:
        _require_public_destination(target_url)
    req_headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"}
    if headers:
        req_headers.update(headers)

    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(target_url, data=data, headers=req_headers, method=method)
    opener = urllib.request.build_opener(_GuardedRedirectHandler(public_only=public_only))
    byte_truncated = False
    try:
        with opener.open(request, timeout=timeout) as resp:
            status_code = int(getattr(resp, "status", 200))
            final_url = _validate_http_url(str(resp.geturl() or target_url))
            content_type = str(resp.headers.get("Content-Type", ""))
            content_encoding = str(resp.headers.get("Content-Encoding", "")).lower()
            chunks = []
            received = 0
            while True:
                chunk = resp.read(min(65536, max_bytes - received + 1))
                if not chunk:
                    break
                remaining = max_bytes - received
                if len(chunk) > remaining:
                    if raise_on_exceed:
                        raise ValueError(f"Response exceeded max_bytes limit ({max_bytes} bytes)")
                    byte_truncated = True
                    chunks.append(chunk[:remaining])
                    break
                chunks.append(chunk)
                received += len(chunk)
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes + 1)
        byte_truncated = len(body) > max_bytes
        if byte_truncated and raise_on_exceed:
            raise ValueError(f"Response exceeded max_bytes limit ({max_bytes} bytes)") from exc
        return (
            int(exc.code),
            target_url,
            str(exc.headers.get("Content-Type", "") if exc.headers else ""),
            body[:max_bytes],
            byte_truncated,
        )

    raw = b"".join(chunks)
    if "gzip" in content_encoding and raw:
        # Read one extra expanded byte to detect clipping without materializing the full payload.
        with contextlib.suppress(zlib.error):
            raw = zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(raw, max_bytes + 1)
            if len(raw) > max_bytes:
                byte_truncated = True
                raw = raw[:max_bytes]
    if byte_truncated and raise_on_exceed:
        raise ValueError(f"Response exceeded max_bytes limit ({max_bytes} bytes)")
    return status_code, final_url, content_type, raw, byte_truncated


class _ReadableHtmlParser(HTMLParser):
    """Stdlib HTML parser that strips boilerplate tags and extracts clean text, title, and links."""

    def __init__(self, base_url: str, *, extract_links: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.extract_links = extract_links
        self._ignore_depth = 0
        self._in_title = False
        self._in_link = False
        self._current_href = ""
        self._link_text: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._seen_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track ignored boilerplate subtrees, headings, title, and anchor tags."""
        lower = tag.lower()
        if self._ignore_depth > 0:
            if lower in _IGNORED_TAGS:
                self._ignore_depth += 1
            return
        if lower in _IGNORED_TAGS:
            self._ignore_depth = 1
            return
        if lower == "title":
            self._in_title = True
            return
        if lower in _BLOCK_TAGS:
            self.text_parts.append("\n")
            if lower in _HEADING_PREFIX:
                self.text_parts.append(_HEADING_PREFIX[lower])
            elif lower == "li":
                self.text_parts.append("- ")
        if lower == "a" and self.extract_links:
            attr_map = dict(attrs)
            href = (attr_map.get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._in_link = True
                self._current_href = urllib.parse.urljoin(self.base_url, href)
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        """Close ignored tags, block boundaries, titles, and anchor elements."""
        lower = tag.lower()
        if self._ignore_depth > 0:
            if lower in _IGNORED_TAGS:
                self._ignore_depth -= 1
            return
        if lower == "title":
            self._in_title = False
        elif lower == "a" and self._in_link:
            self._in_link = False
            resolved = self._current_href
            parts = urllib.parse.urlsplit(resolved)
            if parts.scheme.lower() in {"http", "https"} and resolved not in self._seen_links:
                self._seen_links.add(resolved)
                self.links.append({"text": _collapse_ws("".join(self._link_text)), "url": resolved})
        elif lower in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Collect visible text, title text, and link labels."""
        if self._ignore_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.text_parts.append(data)
        if self._in_link:
            self._link_text.append(data)


class _DDGHtmlParser(HTMLParser):
    """Stdlib HTML parser for DuckDuckGo HTML/Lite search result pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_result_link = False
        self._snippet_depth = 0
        self._snippet_tag = ""
        self._current_title: list[str] = []
        self._current_url = ""
        self._current_snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture DuckDuckGo result title links and snippet blocks."""
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._in_result_link = True
            self._current_url = _unwrap_ddg_url(attr_map.get("href") or "")
            self._current_title = []
        elif self._snippet_depth > 0 and tag == self._snippet_tag:
            self._snippet_depth += 1
        elif (
            self._snippet_depth == 0
            and tag in {"a", "div", "span", "td"}
            and ("result__snippet" in classes or "result-snippet" in classes)
        ):
            self._snippet_depth = 1
            self._snippet_tag = tag
            self._current_snippet = []

    def handle_endtag(self, tag: str) -> None:
        """Finalize collected title links and snippets."""
        if tag == "a" and self._in_result_link:
            self._in_result_link = False
            title = _collapse_ws("".join(self._current_title))
            if title and self._current_url.startswith(("http://", "https://")):
                self.results.append({"title": title, "url": self._current_url, "snippet": ""})
        elif self._snippet_depth > 0 and tag == self._snippet_tag:
            self._snippet_depth -= 1
            if self._snippet_depth == 0:
                snippet = _collapse_ws("".join(self._current_snippet))
                if self.results and not self.results[-1]["snippet"]:
                    self.results[-1]["snippet"] = snippet

    def handle_data(self, data: str) -> None:
        """Accumulate text inside active result title or snippet nodes."""
        if self._in_result_link:
            self._current_title.append(data)
        elif self._snippet_depth > 0:
            self._current_snippet.append(data)


class _PyPISearchParser(HTMLParser):
    """Stdlib HTML parser for PyPI search snippet cards."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_card = False
        self._field: str | None = None
        self._buf: list[str] = []
        self._href = ""
        self._name = ""
        self._version = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track package-snippet anchors and their child name/version/description spans."""
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        if tag == "a" and "package-snippet" in classes:
            self._in_card = True
            self._href = urllib.parse.urljoin("https://pypi.org", attr_map.get("href") or "")
            self._name = ""
            self._version = ""
        elif self._in_card and "package-snippet__name" in classes:
            self._field = "name"
            self._buf = []
        elif self._in_card and "package-snippet__version" in classes:
            self._field = "version"
            self._buf = []
        elif self._in_card and "package-snippet__description" in classes:
            self._field = "description"
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        """Flush captured PyPI package metadata fields."""
        if self._field is not None:
            text = _collapse_ws("".join(self._buf))
            if self._field == "name":
                self._name = text
            elif self._field == "version":
                self._version = text
            elif self._field == "description" and self._name:
                title = f"{self._name} {self._version}".strip()
                self.results.append({"title": title, "url": self._href, "snippet": text})
            self._field = None
            self._buf = []
        elif tag == "a" and self._in_card:
            self._in_card = False

    def handle_data(self, data: str) -> None:
        """Collect text inside an active PyPI metadata element."""
        if self._field is not None:
            self._buf.append(data)


def _collapse_ws(text: str) -> str:
    """Collapse internal runs of whitespace into single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _clean_multiline_text(text: str) -> str:
    """Normalize line whitespace while preserving paragraph breaks."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if not line:
            blank_run += 1
            if blank_run <= 1 and cleaned:
                cleaned.append("")
        else:
            blank_run = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _unwrap_ddg_url(href: str) -> str:
    """Unwrap DuckDuckGo redirect URLs (`/l/?uddg=...`) into target URLs."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlsplit(href)
    if "duckduckgo.com" in parsed.netloc or parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("uddg", [""])[0]
        if target:
            return target
    return href


def _extract_html_with_bs4(
    html_text: str, base_url: str, *, extract_links: bool
) -> tuple[str, str, list[dict[str, str]]]:
    """Extract title, readable text, and optional links using BeautifulSoup when installed."""
    bs4_mod = _try_import("bs4")
    if bs4_mod is None:
        parser = _ReadableHtmlParser(base_url, extract_links=extract_links)
        parser.feed(html_text)
        parser.close()
        title = _collapse_ws("".join(parser.title_parts))
        content = _clean_multiline_text("".join(parser.text_parts))
        return title, content, parser.links

    soup = bs4_mod.BeautifulSoup(html_text, "html.parser")
    title = _collapse_ws(soup.title.get_text(" ", strip=True)) if soup.title else ""
    for tag in soup.find_all(list(_IGNORED_TAGS)):
        tag.decompose()
    links: list[dict[str, str]] = []
    if extract_links:
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            resolved = urllib.parse.urljoin(base_url, href)
            if urllib.parse.urlsplit(resolved).scheme.lower() in {"http", "https"} and resolved not in seen:
                seen.add(resolved)
                links.append({"text": _collapse_ws(anchor.get_text(" ", strip=True)), "url": resolved})
    for heading_tag, prefix in _HEADING_PREFIX.items():
        for node in soup.find_all(heading_tag):
            text = _collapse_ws(node.get_text(" ", strip=True))
            if text:
                node.replace_with(f"\n{prefix}{text}\n")
    raw_text = soup.get_text("\n", strip=True)
    return title, _clean_multiline_text(raw_text), links


def _format_search_results(query: str, provider: str, results: list[dict[str, str]]) -> str:
    """Format structured search results into a concise Markdown summary."""
    lines = [f"Search results for {query!r} via {provider} ({len(results)} results):"]
    for idx, item in enumerate(results, start=1):
        lines.append(f"{idx}. {item['title']} — {item['url']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
    return "\n".join(lines)


def _search_host(query: str, num_results: int) -> list[dict[str, str]]:
    """Run the host-mediated `web_search` builtin of the Fleet REPL that loaded this module.

    Search keys live on the host. Raises when the module runs outside the REPL, no key is
    configured, or the host call fails.
    """
    repl_module = sys.modules.get("__fleet_repl__")
    host_search = getattr(repl_module, "web_search", None)
    if not callable(host_search):
        raise RuntimeError("host web_search is unavailable outside the Fleet REPL")
    return list(host_search(query, min(num_results, _HOST_SEARCH_MAX_RESULTS)))[:num_results]


def _parse_ddg_html(html_text: str, num_results: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML or Lite pages using BeautifulSoup when present or stdlib HTMLParser."""
    bs4_mod = _try_import("bs4")
    if bs4_mod is not None:
        soup = bs4_mod.BeautifulSoup(html_text, "html.parser")
        results: list[dict[str, str]] = []
        for container in soup.select(".result, .web-result"):
            link = container.select_one("a.result__a, a.result-link")
            if link is None:
                continue
            target = _unwrap_ddg_url(str(link.get("href") or ""))
            if not target.startswith(("http://", "https://")) or "duckduckgo.com/y.js" in target:
                continue
            snippet_node = container.select_one(".result__snippet, .result-snippet")
            results.append(
                {
                    "title": _collapse_ws(link.get_text(" ", strip=True)),
                    "url": target,
                    "snippet": _collapse_ws(snippet_node.get_text(" ", strip=True)) if snippet_node else "",
                }
            )
            if len(results) >= num_results:
                break
        if results:
            return results

    parser = _DDGHtmlParser()
    parser.feed(html_text)
    parser.close()
    return [r for r in parser.results if "duckduckgo.com/y.js" not in r["url"]][:num_results]


def _search_ddg(query: str, num_results: int, timeout: int) -> list[dict[str, str]]:
    """Search DuckDuckGo HTML and Lite endpoints without an API key."""
    endpoints = (
        ("https://html.duckduckgo.com/html/", {"q": query}),
        ("https://lite.duckduckgo.com/lite/", {"q": query}),
    )
    for endpoint, params in endpoints:
        status, _, _, raw, _ = _http_request(
            endpoint,
            params=params,
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
        )
        if status >= 400 or status == 202:
            continue
        text = raw.decode("utf-8", errors="replace")
        if "anomaly-modal" in text or "challenge-form" in text:
            continue
        results = _parse_ddg_html(text, num_results)
        if results:
            return results
    return []


def _search_wikipedia(query: str, num_results: int, timeout: int) -> list[dict[str, str]]:
    """Search English Wikipedia via the keyless MediaWiki query API."""
    status, _, _, raw, _ = _http_request(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": num_results,
            "format": "json",
            "utf8": 1,
        },
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    if status >= 400:
        raise RuntimeError(f"Wikipedia API returned HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    results: list[dict[str, str]] = []
    for item in data.get("query", {}).get("search", [])[:num_results]:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        raw_snippet = html.unescape(str(item.get("snippet", "")))
        snippet = _collapse_ws(re.sub(r"<[^>]+>", "", raw_snippet))
        url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        results.append({"title": title, "url": url, "snippet": snippet})
    return results


def _search_arxiv(query: str, num_results: int, timeout: int) -> list[dict[str, str]]:
    """Search arXiv preprints via the public Atom export API."""
    status, _, _, raw, _ = _http_request(
        "https://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "start": 0, "max_results": num_results},
        headers={"Accept": "application/atom+xml,application/xml"},
        timeout=timeout,
    )
    if status >= 400:
        raise RuntimeError(f"arXiv API returned HTTP {status}")
    root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", ns)[:num_results]:
        title = _collapse_ws(entry.findtext("atom:title", default="", namespaces=ns))
        url = _collapse_ws(entry.findtext("atom:id", default="", namespaces=ns))
        summary = _collapse_ws(entry.findtext("atom:summary", default="", namespaces=ns))
        if url:
            results.append({"title": title, "url": url, "snippet": summary[:400]})
    return results


def _search_github(query: str, num_results: int, timeout: int) -> list[dict[str, str]]:
    """Search public GitHub repositories via the REST search API."""
    status, _, _, raw, _ = _http_request(
        "https://api.github.com/search/repositories",
        params={"q": query, "per_page": num_results},
        headers={"Accept": "application/vnd.github+json"},
        timeout=timeout,
    )
    if status >= 400:
        raise RuntimeError(f"GitHub API returned HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    results: list[dict[str, str]] = []
    for item in data.get("items", [])[:num_results]:
        url = str(item.get("html_url", "")).strip()
        if not url:
            continue
        desc = str(item.get("description") or "").strip()
        stars = item.get("stargazers_count", 0)
        lang = item.get("language") or "unknown"
        snippet = f"{desc} (stars: {stars}, language: {lang})".strip()
        results.append({"title": str(item.get("full_name", "")).strip(), "url": url, "snippet": snippet})
    return results


def _search_pypi(query: str, num_results: int, timeout: int) -> list[dict[str, str]]:
    """Search PyPI packages via HTML snippets, falling back to exact package JSON lookup."""
    status, _, _, raw, _ = _http_request(
        "https://pypi.org/search/",
        params={"q": query},
        headers={"Accept": "text/html"},
        timeout=timeout,
    )
    if status < 400:
        parser = _PyPISearchParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        parser.close()
        if parser.results:
            return parser.results[:num_results]

    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", query.strip()).strip("-")
    if not slug:
        return []
    pkg_status, _, _, pkg_raw, _ = _http_request(
        f"https://pypi.org/pypi/{urllib.parse.quote(slug)}/json",
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    if pkg_status >= 400:
        return []
    info = json.loads(pkg_raw.decode("utf-8")).get("info", {})
    name = str(info.get("name", slug)).strip()
    version = str(info.get("version", "")).strip()
    summary = str(info.get("summary", "")).strip()
    url = str(info.get("package_url") or f"https://pypi.org/project/{name}/").strip()
    return [{"title": f"{name} {version}".strip(), "url": url, "snippet": summary}]


def search_web(
    query: str,
    *,
    provider: str = "auto",
    num_results: int = 5,
    timeout: int = 20,
) -> dict[str, Any]:
    """Search the web or vertical knowledge indexes and return structured results.

    Args:
        query: Search query string.
        provider: One of ``auto``, ``ddg``, ``wikipedia``, ``arxiv``, ``github``,
            or ``pypi``. ``auto`` tries the host ``web_search`` builtin first.
        num_results: Maximum number of results to return.
        timeout: Network timeout in seconds.

    Returns:
        Dictionary containing ``query``, ``provider``, ``results``, and ``formatted``.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if isinstance(num_results, bool) or not isinstance(num_results, int) or num_results < 1:
        raise ValueError("num_results must be a positive integer")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unsupported provider {provider!r}; expected one of {sorted(_VALID_PROVIDERS)}")

    cleaned_query = query.strip()
    results: list[dict[str, str]] = []

    selected = provider
    if selected == "auto":
        # The bridge preserves error messages in RuntimeError subclasses. Only an
        # unavailable host search permits fallback; budget/provider failures must propagate.
        try:
            results = _search_host(cleaned_query, num_results)
        except RuntimeError as exc:
            if str(exc) not in {"web_search not configured", "host web_search is unavailable outside the Fleet REPL"}:
                raise
            results = []
        selected = str(results[0].get("provider") or "web_search") if results else "ddg"

    if selected == "ddg":
        try:
            results = _search_ddg(cleaned_query, num_results, timeout)
        except (OSError, RuntimeError, ValueError, TimeoutError):
            results = []
        if not results and provider == "auto":
            selected = "wikipedia"
            results = _search_wikipedia(cleaned_query, num_results, timeout)
    elif selected == "wikipedia":
        results = _search_wikipedia(cleaned_query, num_results, timeout)
    elif selected == "arxiv":
        results = _search_arxiv(cleaned_query, num_results, timeout)
    elif selected == "github":
        results = _search_github(cleaned_query, num_results, timeout)
    elif selected == "pypi":
        results = _search_pypi(cleaned_query, num_results, timeout)

    formatted = _format_search_results(cleaned_query, selected, results)
    return {
        "query": cleaned_query,
        "provider": selected,
        "results": results,
        "formatted": formatted,
    }


def fetch_page(
    url: str,
    *,
    max_chars: int = 20_000,
    max_bytes: int = 2 * 1024 * 1024,
    extract_links: bool = False,
    timeout: int = 20,
) -> dict[str, Any]:
    """Fetch a bounded HTTP/HTTPS page and extract readable text and optional links.

    Args:
        url: Target HTTP or HTTPS URL.
        max_chars: Maximum character length of ``content`` returned.
        max_bytes: Maximum byte payload read from the HTTP response.
        extract_links: Whether to return discovered HTTP/HTTPS links.
        timeout: Network timeout in seconds.

    Returns:
        Dictionary with ``url``, ``status_code``, ``content_type``, ``title``,
        ``content``, ``truncated``, ``total_chars``, and ``links``.
        ``truncated`` includes download, gzip expansion, and character limits.
        ``total_chars`` counts extracted text after byte limiting, before character limiting;
        it is not the full page length when bytes were discarded.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    status_code, final_url, content_type, raw, byte_truncated = _http_request(
        url,
        headers={"Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5"},
        max_bytes=max_bytes,
        timeout=timeout,
        public_only=True,
    )
    charset = _charset_from_content_type(content_type)
    decoded = raw.decode(charset, errors="replace")
    lower_ct = content_type.lower()

    title = ""
    links: list[dict[str, str]] = []
    if "json" in lower_ct:
        try:
            full_text = json.dumps(json.loads(decoded), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            full_text = decoded
    elif "html" in lower_ct or (not lower_ct and "<html" in decoded[:512].lower()):
        title, full_text, links = _extract_html_with_bs4(decoded, final_url, extract_links=extract_links)
    else:
        full_text = decoded.strip()

    total_chars = len(full_text)
    truncated = byte_truncated or total_chars > max_chars
    return {
        "url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "title": title,
        "content": full_text[:max_chars],
        "truncated": truncated,
        "total_chars": total_chars,
        "links": links if extract_links else [],
    }


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = 2 * 1024 * 1024,
    timeout: int = 20,
) -> Any:
    """Fetch a bounded JSON payload from an HTTP/HTTPS endpoint.

    Args:
        url: Target HTTP or HTTPS URL.
        params: Optional query parameters.
        headers: Optional request headers.
        max_bytes: Maximum allowed response size in bytes.
        timeout: Network timeout in seconds.

    Returns:
        Parsed JSON value.
    """
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    status_code, _, content_type, raw, _ = _http_request(
        url,
        params=params,
        headers=req_headers,
        max_bytes=max_bytes,
        timeout=timeout,
        raise_on_exceed=True,
        public_only=True,
    )
    if status_code >= 400:
        raise RuntimeError(f"HTTP {status_code} fetching JSON from {url}")
    charset = _charset_from_content_type(content_type)
    try:
        return json.loads(raw.decode(charset, errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON payload from {url}: {exc}") from exc


def run(
    query_or_url: str,
    *,
    num_results: int = 5,
    max_chars: int = 20_000,
    timeout: int = 20,
) -> dict[str, Any]:
    """Dispatch URLs to ``fetch_page`` and search queries to ``search_web``.

    Args:
        query_or_url: An HTTP/HTTPS URL to fetch or a search query string.
        num_results: Result count when searching.
        max_chars: Maximum content characters when fetching a page.
        timeout: Network timeout in seconds.

    Returns:
        Structured page or search result dictionary.
    """
    if not isinstance(query_or_url, str) or not query_or_url.strip():
        raise ValueError("query_or_url must be a non-empty string")
    stripped = query_or_url.strip()
    if stripped.lower().startswith(("http://", "https://")):
        return fetch_page(stripped, max_chars=max_chars, timeout=timeout)
    return search_web(stripped, num_results=num_results, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint for web search and page fetching."""
    parser = argparse.ArgumentParser(description="Search the web or fetch bounded pages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search using auto or explicit provider.")
    search_parser.add_argument("query", help="Search query string.")
    search_parser.add_argument("--provider", default="auto", choices=sorted(_VALID_PROVIDERS))
    search_parser.add_argument("--num-results", type=int, default=5)
    search_parser.add_argument("--timeout", type=int, default=20)
    search_parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")

    fetch_parser = subparsers.add_parser("fetch", help="Fetch and clean an HTTP/HTTPS page.")
    fetch_parser.add_argument("url", help="Target HTTP/HTTPS URL.")
    fetch_parser.add_argument("--max-chars", type=int, default=20_000)
    fetch_parser.add_argument("--extract-links", action="store_true")
    fetch_parser.add_argument("--timeout", type=int, default=20)
    fetch_parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")

    args = parser.parse_args(argv)
    if args.command == "search":
        result = search_web(args.query, provider=args.provider, num_results=args.num_results, timeout=args.timeout)
        output = json.dumps(result, indent=2, ensure_ascii=False) if args.as_json else result["formatted"]
    else:
        result = fetch_page(args.url, max_chars=args.max_chars, extract_links=args.extract_links, timeout=args.timeout)
        if args.as_json:
            output = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            title_line = f"# {result['title']}\n" if result["title"] else ""
            output = f"{title_line}URL: {result['url']}\n\n{result['content']}"
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
