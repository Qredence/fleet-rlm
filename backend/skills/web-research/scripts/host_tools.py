"""Public web research tools. One instance and credit budget per workspace turn."""

import atexit
from ipaddress import ip_address
from threading import Lock
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

_client = httpx.Client(timeout=20)
atexit.register(_client.close)


def _public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None:
            return False
        try:
            return ip_address(host).is_global
        except ValueError:
            return "." in host and not host.endswith((".localhost", ".local", ".internal"))
    except ValueError:
        return False


class WebResearch:
    def __init__(self, key: SecretStr):
        self._key = key
        self._remaining = 8
        self._lock = Lock()

    def _budget(self, *, requested: bool) -> dict:
        return {"used": 8 - self._remaining, "limit": 8, "requested": requested}

    def _invalid(self, message: str) -> dict:
        with self._lock:
            return {
                "error": message,
                "error_code": "invalid_input",
                "budget": self._budget(requested=False),
            }

    def _request(self, endpoint: str, payload: dict) -> dict:
        with self._lock:
            if self._remaining == 0:
                return {
                    "error": "Web research reached its eight-call limit for this turn.",
                    "error_code": "allowance_exhausted",
                    "budget": self._budget(requested=False),
                }
            self._remaining -= 1
            budget = self._budget(requested=True)
        try:
            response = _client.post(
                f"https://api.tavily.com/{endpoint}",
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Unexpected provider response")
            return {"results": data["results"], "budget": budget}
        except httpx.HTTPStatusError as error:
            message = f"Tavily {endpoint} returned HTTP {error.response.status_code}."
        except (httpx.RequestError, ValueError):
            message = f"Tavily {endpoint} did not return a readable response."
        return {"error": message, "error_code": "provider_error", "budget": budget}

    def web_search(self, query: str) -> dict:
        """Find public sources. Returns titles, URLs and excerpts for discovery."""
        if not query.strip():
            return self._invalid("Provide a search query.")
        data = self._request("search", {"query": query, "max_results": 5, "include_answer": False})
        if "error" in data:
            return data
        try:
            results = [
                {"title": item["title"], "url": item["url"], "snippet": item["content"][:800]}
                for item in data["results"][:5]
            ]
        except (KeyError, TypeError):
            return {
                "error": "Tavily search returned malformed results.",
                "error_code": "provider_error",
                "budget": data["budget"],
            }
        return {"results": results, "budget": data["budget"]}

    def web_open(self, url: str) -> dict:
        """Read a public page. Returns source URL and Markdown or an extraction error."""
        if not _public_url(url):
            return self._invalid("Provide a public HTTP or HTTPS page URL.")
        data = self._request("extract", {"urls": [url], "format": "markdown"})
        if "error" in data:
            return data
        budget = data["budget"]
        results = data["results"]
        page = results[0] if results else {}
        if not isinstance(page, dict) or not isinstance(page.get("raw_content"), str):
            return {"url": url, "error": "Tavily could not extract this page.", "budget": budget}
        content = page["raw_content"]
        if not content.strip():
            return {"url": url, "error": "The page returned no readable text.", "budget": budget}
        return {
            "url": page.get("url") or url,
            "markdown": content[:16000],
            "truncated": len(content) > 16000,
            "budget": budget,
        }


def bind_tools(settings):
    """Build a fresh shared allowance for this turn and its children."""
    if not settings.tavily_api_key:
        return {}
    web = WebResearch(settings.tavily_api_key)
    return {"web_search": web.web_search, "web_open": web.web_open}
