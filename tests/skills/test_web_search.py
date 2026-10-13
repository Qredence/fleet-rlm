"""Host-key search contracts, including concurrent spend and revoked Turns."""

import asyncio
import json
import time

import httpx
import pytest
from pydantic import SecretStr

from fleet_rlm.web_search import TavilySearch, WebSearchToolHost


@pytest.mark.asyncio
async def test_search_disables_synthesis_and_bounds_untrusted_results() -> None:
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "answer": "provider synthesis is not returned",
                "results": [
                    {"url": "https://example.org/source", "title": "a" * 1000, "content": "b" * 5000},
                    {"url": "file:///private", "title": "not a web source"},
                    {"url": "https://user:password@example.org", "title": "credentialed"},
                ],
            },
        )

    client = TavilySearch(SecretStr("host-only-secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True)
        result = await host.web_search("public query", max_results=5, depth="advanced")
        assert result["ok"] is True
        assert len(result["results"]) == 1
        assert len(result["results"][0]["title"]) == 256
        assert len(result["results"][0]["snippet"]) == 1600
        assert result["coverage"] == "search_snippets"
        assert result["credits_remaining"] == 60
        assert "answer" not in result
        assert "host-only-secret" not in repr(result)
        assert seen[0].headers["Authorization"] == "Bearer host-only-secret"
        body = json.loads(seen[0].content)
        assert body["include_answer"] is False
        assert body["include_raw_content"] is False
        assert body["auto_parameters"] is False
        view = host.event_views()["web_search"]
        assert view.input({"query": "public query"}) == view.output(result) == {}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_parallel_search_reserves_credits_before_dispatch_and_keeps_failures_charged() -> None:
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(429, text="private provider diagnostics")

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True, credit_limit=3)
        results = await asyncio.gather(*(host.web_search(f"query {i}", depth="advanced") for i in range(10)))
        assert len(calls) == 1
        assert sum(result.get("error") == "search_budget_exhausted" for result in results) == 9
        assert "private provider diagnostics" not in repr(results)
        last = await host.web_search("one remaining credit")
        assert last["credits_remaining"] == 0
        assert (await host.web_search("cannot dispatch"))["error"] == "search_budget_exhausted"
        assert len(calls) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_revoke_during_request_discards_results_and_does_not_retry() -> None:
    authorized = True
    calls = []

    async def respond(request):
        nonlocal authorized
        calls.append(request)
        authorized = False
        return httpx.Response(200, json={"results": [{"url": "https://example.org", "content": "late"}]})

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: authorized)
        assert await host.web_search("query") == {"ok": False, "error": "search_no_longer_authorized"}
        assert await host.web_search("query") == {"ok": False, "error": "search_no_longer_authorized"}
        assert len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 500, 302])
async def test_provider_errors_are_safe_and_redirects_are_not_followed(status) -> None:
    client = TavilySearch(
        SecretStr("secret"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status, text="secret provider body", headers={"Location": "http://localhost"}
            )
        ),
    )
    try:
        result = await client.search("query", 5, "basic", deadline=time.monotonic() + 20)
        assert result == {"ok": False, "error": "search_unavailable"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_provider_response_bytes_are_bounded_and_bad_arguments_do_not_dispatch() -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 200001)

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True)
        for arguments in ({"query": ""}, {"query": "x", "max_results": True}, {"query": "x", "depth": "auto"}):
            assert (await host.web_search(**arguments))["error"] == "invalid_search_arguments"
        assert calls == []
        assert (await host.web_search("valid"))["error"] == "response_too_large"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_primary_source_domains_restrict_search_and_filter_provider_results():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://www.daytona.io/docs/en/persistence/", "content": "primary"},
                    {"url": "https://github.com/daytonaio/daytona", "content": "source"},
                    {"url": "https://notdaytona.io/blog", "content": "outside scope"},
                    {"url": "https://daytona.io.evil.example/page", "content": "suffix spoof"},
                ]
            },
        )

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True)
        result = await host.web_search("official persistence docs", include_domains=["Daytona.IO", "github.com"])
        body = json.loads(calls[0].content)
        assert body["include_domains"] == ["daytona.io", "github.com"]
        # Tavily documents include_domains only; restriction is enforced again on results.
        assert "include_domains_mode" not in body
        assert [item["url"] for item in result["results"]] == [
            "https://www.daytona.io/docs/en/persistence/",
            "https://github.com/daytonaio/daytona",
        ]
        assert result["credits_reserved"] == 1
        assert result["credits_remaining"] == 61
        assert "include_domains" in host.as_tools()[0].args
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domains",
    [
        "daytona.io",
        ["https://daytona.io"],
        ["daytona.io/path"],
        ["user:secret@daytona.io"],
        ["localhost"],
        ["a.internal"],
        ["127.0.0.1"],
        [False],
        ["x.com"] * 11,
    ],
)
async def test_invalid_domain_filters_do_not_dispatch_or_reserve_credits(domains):
    calls = []
    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(lambda request: calls.append(request)))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True)
        result = await host.web_search("public query", include_domains=domains)
        assert result == {"ok": False, "error": "invalid_search_arguments"}
        assert calls == []
        assert host._reserved == 0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_general_search_without_domain_filter_keeps_unrestricted_results():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [{"url": "https://example.org", "content": "general"}]})

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True)
        result = await host.web_search("general research")
        assert result["results"][0]["url"] == "https://example.org"
        assert "include_domains" not in json.loads(calls[0].content)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_credits_remaining_is_the_balance_at_this_calls_reservation() -> None:
    first_sent = asyncio.Event()
    release = asyncio.Event()

    async def respond(request):
        if json.loads(request.content)["query"] == "first":
            first_sent.set()
            await release.wait()
        return httpx.Response(200, json={"results": []})

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        host = WebSearchToolHost(client, time.monotonic() + 20, lambda: True, credit_limit=5)
        first = asyncio.create_task(host.web_search("first"))
        await asyncio.wait_for(first_sent.wait(), timeout=5)
        second = await host.web_search("second", depth="advanced")
        release.set()
        assert (await first)["credits_remaining"] == 4
        assert second["credits_remaining"] == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_transport_failure_logs_only_its_type(caplog: pytest.LogCaptureFixture) -> None:
    def respond(_request):
        raise httpx.ConnectError("private provider host detail")

    client = TavilySearch(SecretStr("secret"), transport=httpx.MockTransport(respond))
    try:
        with caplog.at_level("DEBUG", logger="fleet_rlm.web_search"):
            result = await client.search("q", 1, "basic", deadline=time.monotonic() + 5)
        assert result == {"ok": False, "error": "search_unavailable"}
        assert "ConnectError" in caplog.text
        assert "private provider host detail" not in caplog.text
    finally:
        await client.aclose()
