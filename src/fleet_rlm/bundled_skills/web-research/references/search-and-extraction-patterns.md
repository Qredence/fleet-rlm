# Search and extraction patterns

## Choosing a search provider

| Provider | When to use | Key required |
| --- | --- | --- |
| `auto` | Host search first; if unavailable or empty, `ddg` then `wikipedia`. Other host errors propagate. | Optional |
| `web_search` (REPL builtin) | Keyed search through the host (Serper, then You.com); keys stay on the server | Configured on the host |
| `ddg` | Keyless general web search via DuckDuckGo HTML/Lite | No |
| `wikipedia` | Encyclopedic topics, concepts, historical facts | No |
| `arxiv` | Research papers, preprints, ML/systems literature | No |
| `github` | Open-source repositories, stars, languages, project URLs | No |
| `pypi` | Python package versions, summaries, and project links | No |

## Fetching and filtering pages cleanly

1. Start with `search_web(...)` to identify 2-4 authoritative URLs rather than blindly crawling.
2. Call `fetch_page(url, extract_links=True)` to inspect headings, clean text, and follow-up documentation links on the same host.
3. For JSON endpoints (APIs, GitHub raw metadata, PyPI JSON), prefer `fetch_json(url, params=...)` so parsing errors and oversized payloads fail loudly.
4. Combine fetched passages in Python variables, or batch semantic checks across multiple extracted pages with `llm_query_batched(prompts)` rather than printing full pages into the REPL log.
5. Save verified findings or source tables with `workspace.write_artifact("sources.json", ...)` and verify URLs and quotes directly from variables before `SUBMIT`.
