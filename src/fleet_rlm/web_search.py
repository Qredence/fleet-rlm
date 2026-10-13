"""Optional host-key Tavily search; page retrieval stays inside Daytona."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import dspy
import httpx
from pydantic import SecretStr

from fleet_rlm.tool_events import ToolEventView

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.tavily.com/search"
_MAX_RESPONSE_BYTES = 200_000
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _search_domains(value: list[str] | None) -> tuple[str, ...]:
    """Normalize an optional source restriction without accepting URLs or credentials."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError("invalid source domains")
    domains = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("invalid source domain")
        domain = raw.strip().lower().rstrip(".")
        labels = domain.split(".")
        if (
            len(domain) > 253
            or len(labels) < 2
            or labels[-1].isdigit()
            or domain.endswith((".localhost", ".local", ".internal"))
            or any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels)
        ):
            raise ValueError("invalid source domain")
        if domain not in domains:
            domains.append(domain)
    return tuple(domains)


class TavilySearch:
    """One application-owned HTTP client; its credential never enters the REPL."""

    def __init__(self, api_key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            transport=transport,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        max_results: int,
        depth: str,
        *,
        deadline: float,
        include_domains: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        remaining = min(20.0, deadline - time.monotonic())
        if remaining <= 0:
            return {"ok": False, "error": "deadline_exceeded"}
        parameters: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": depth,
            "include_answer": False,
            "include_raw_content": False,
            "auto_parameters": False,
        }
        if include_domains:
            parameters["include_domains"] = list(include_domains)
        try:
            async with asyncio.timeout(remaining):
                async with self._client.stream(
                    "POST",
                    _SEARCH_URL,
                    json=parameters,
                    timeout=remaining,
                ) as response:
                    if response.status_code != 200:
                        error = "rate_limited" if response.status_code == 429 else "search_unavailable"
                        return {"ok": False, "error": error}
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            return {"ok": False, "error": "response_too_large"}
                payload = json.loads(body)
                raw_results = payload.get("results") if isinstance(payload, Mapping) else None
                if not isinstance(raw_results, list):
                    return {"ok": False, "error": "invalid_search_response"}
                results = []
                for item in raw_results[:max_results]:
                    if not isinstance(item, Mapping) or not isinstance(item.get("url"), str):
                        continue
                    url = item["url"]
                    parts = urlsplit(url)
                    if (
                        len(url) > 4096
                        or parts.scheme not in {"http", "https"}
                        or not parts.hostname
                        or parts.username is not None
                        or parts.password is not None
                    ):
                        continue
                    hostname = parts.hostname.lower().rstrip(".")
                    if include_domains and not any(
                        hostname == domain or hostname.endswith("." + domain) for domain in include_domains
                    ):
                        continue
                    results.append(
                        {
                            "title": str(item.get("title") or "")[:256],
                            "url": url,
                            "snippet": str(item.get("content") or "")[:1600],
                        }
                    )
                return {"ok": True, "provider": "tavily", "coverage": "search_snippets", "results": results}
        except TimeoutError:
            return {"ok": False, "error": "search_timeout"}
        except (httpx.HTTPError, ValueError) as exc:
            # Provider bodies, request headers and exception text are private.
            logger.debug("Tavily search failed error_type=%s", type(exc).__name__)
            return {"ok": False, "error": "search_unavailable"}


@dataclass(slots=True)
class WebSearchToolHost:
    """A Turn-local credit reservation counter over the application client."""

    client: TavilySearch
    deadline: float
    is_authorized: Callable[[], bool]
    credit_limit: int = 62
    _reserved: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        depth: Literal["basic", "advanced"] = "basic",
        include_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            domains = _search_domains(include_domains)
        except ValueError:
            return {"ok": False, "error": "invalid_search_arguments"}
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 4096
            or type(max_results) is not int
            or not 1 <= max_results <= 10
            or depth not in {"basic", "advanced"}
        ):
            return {"ok": False, "error": "invalid_search_arguments"}
        async with self._lock:
            if not self.is_authorized() or time.monotonic() >= self.deadline:
                return {"ok": False, "error": "search_no_longer_authorized"}
            charge = 2 if depth == "advanced" else 1
            if self._reserved + charge > self.credit_limit:
                return {"ok": False, "error": "search_budget_exhausted"}
            self._reserved += charge
            remaining = self.credit_limit - self._reserved
        # Retain the reservation on failure: a disconnected request may have
        # reached the provider. No retries or secondary providers are implicit.
        result = await self.client.search(
            query.strip(), max_results, depth, deadline=self.deadline, include_domains=domains
        )
        if not self.is_authorized() or time.monotonic() >= self.deadline:
            return {"ok": False, "error": "search_no_longer_authorized"}
        return {**result, "credits_reserved": charge, "credits_remaining": remaining}

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        return (
            dspy.Tool(
                self.web_search,
                name="web_search",
                desc=(
                    "Discover public web sources with Tavily. Supply a public search query, max_results (1-10), "
                    "and depth basic (1 credit) or advanced (2). Successful responses contain ok, "
                    "results (items with title, url, snippet), credits_reserved and credits_remaining. "
                    "Snippets are unverified; "
                    "optionally restrict primary sources with include_domains (up to 10 domain names, no URLs). "
                    "retrieve and verify selected pages in the Sandbox. "
                    "Never send credentials or private source bodies."
                ),
            ),
        )

    def event_views(self) -> dict[str, ToolEventView]:
        return {"web_search": ToolEventView.metadata_only()}
