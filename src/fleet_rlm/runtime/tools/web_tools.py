from __future__ import annotations

import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.runtime.tools.document_tools import _validate_download_url, _ValidatingRedirectHandler
from fleet_rlm.runtime.tools.schemas import (
    FetchPageInput,
    FetchPageOutput,
    WebSearchInput,
    WebSearchOutput,
    WebSearchResult,
)

_SEARCH_TIMEOUT_S = 20
_FETCH_TIMEOUT_S = 30
_MAX_PAGE_BYTES = 5 * 1024 * 1024


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag.lower() in {"p", "br", "li", "div", "section", "article", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        joined = " ".join(self._parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        return re.sub(r"\n\s*", "\n", joined).strip()


def _brave_search(query: str, max_results: int) -> list[WebSearchResult]:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY or BRAVE_API_KEY is required for web_search.")
    params = urllib.parse.urlencode({"q": query, "count": max(1, min(max_results, 20))})
    request = urllib.request.Request(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        method="GET",
    )
    ssl_ctx = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=_SEARCH_TIMEOUT_S, context=ssl_ctx) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    items = payload.get("web", {}).get("results", [])
    results: list[WebSearchResult] = []
    for item in items[:max_results]:
        results.append(
            WebSearchResult(
                url=str(item.get("url", "")),
                title=str(item.get("title", "")),
                snippet=str(item.get("description", "")),
            )
        )
    return results


def _open_fetch_request(request: urllib.request.Request, *, timeout: int, context: ssl.SSLContext) -> Any:
    opener = urllib.request.build_opener(
        _ValidatingRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )
    return opener.open(request, timeout=timeout)


@tool_fn
def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the public web and return result URLs, titles, and snippets."""
    try:
        validated = WebSearchInput(query=query, max_results=max_results)
        results = _brave_search(validated.query, validated.max_results)
        output = WebSearchOutput(status="ok", provider="brave", results=results, count=len(results))
        return output.model_dump()
    except Exception as exc:
        output = WebSearchOutput(status="error", provider="brave", error=str(exc), results=[], count=0)
        return output.model_dump()


@tool_fn
def fetch_page(url: str) -> dict[str, Any]:
    """Fetch a public HTTP(S) page and extract readable text."""
    try:
        validated = FetchPageInput(url=url)
        _validate_download_url(validated.url)
        request = urllib.request.Request(validated.url, headers={"User-Agent": "fleet-rlm/1.0"}, method="GET")
        ssl_ctx = ssl.create_default_context()
        with _open_fetch_request(request, timeout=_FETCH_TIMEOUT_S, context=ssl_ctx) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(_MAX_PAGE_BYTES + 1)
        if len(raw) > _MAX_PAGE_BYTES:
            output = FetchPageOutput(
                status="error", url=validated.url, error=f"Page exceeds {_MAX_PAGE_BYTES} byte limit."
            )
            return output.model_dump()
        charset = "utf-8"
        match = re.search(r"charset=([^;]+)", content_type, flags=re.IGNORECASE)
        if match:
            charset = match.group(1).strip()
        try:
            html = raw.decode(charset, errors="replace")
        except LookupError:
            html = raw.decode("utf-8", errors="replace")
        extractor = _TextExtractor()
        extractor.feed(html)
        text = extractor.text()
        output = FetchPageOutput(status="ok", url=validated.url, text=text, char_count=len(text))
        return output.model_dump()
    except Exception as exc:
        output = FetchPageOutput(status="error", url=validated.url, error=str(exc))
        return output.model_dump()


__all__ = ["fetch_page", "web_search"]
