"""Browser automation tool for JavaScript-rendered page fetching.

Exposes ``browser_fetch_page`` marked with ``@tool_fn`` so that
``discover_tools()`` can collect it. The concrete execution is delegated
to the Daytona interpreter (Playwright runs inside the sandbox); calling
this function directly raises ``RuntimeError``.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn


@tool_fn
def browser_fetch_page(
    url: str,
    wait_until: str = "networkidle",
    extract_links: bool = False,
) -> dict[str, Any]:
    """Fetch a JavaScript-rendered web page using a headless Chromium browser.

    Use when fetch_page or fetch_document_text returns empty/SPA shell content.
    Requires a browser-capable sandbox (fleet-rlm-browser snapshot).

    Args:
        url: Public HTTP(S) URL to render and extract text from.
        wait_until: Playwright load state — "networkidle", "load", or "domcontentloaded".
        extract_links: Whether to also extract anchor links from the page.

    Returns:
        Dict with status, url, title, text, char_count, and optionally links.
    """
    raise RuntimeError(
        "browser_fetch_page requires an active AgentRuntime with a browser-capable "
        "Daytona sandbox (fleet-rlm-browser snapshot)."
    )


__all__ = ["browser_fetch_page"]
