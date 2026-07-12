---
name: browser-interaction
description: "Fetch and inspect JavaScript-heavy pages with Playwright in a browser-capable Daytona snapshot. Use when static HTTP fetch returns empty or incomplete HTML."
---

# Browser interaction (optional)

Use when plain `urllib` / `requests` returns empty or incomplete HTML (SPAs, dynamic docs).

## When to use what

| Approach | Use when |
|----------|----------|
| Python `urllib` / `requests` | Static HTML, APIs, raw text |
| Host-fetched attachment / document text | Content already staged as a tool or variable |
| Playwright | Client-rendered pages, screenshots, visible text |

## Patterns

```python
# After navigation, always print bounded excerpts for the RLM loop.
print(page.title())
print(page.inner_text("body")[:4000])
```

- Wait for network idle or a known selector before extracting text.
- Bound extracted text; use `llm_query` on excerpts, not full page dumps.
- Prefer writing evidence under the volume mount (`artifacts/` or run artifacts dir).

## Safety

- Do not exfiltrate credentials, cookies, or environment variables.
- Do not automate logins unless the user explicitly provided in-scope test credentials.
- Cap page size and iteration count.

## Clean notes

- Snapshot selection (`fleet-rlm-browser` vs default) is a host/session concern; do not assume live auto-selection.
- Skills load via host `load_skill(skill_id)` + SkillCards, not volume seed paths under `/home/daytona/memory/`.
