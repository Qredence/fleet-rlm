---
name: browser-interaction
description: "Fetch and inspect JavaScript-heavy pages with Playwright in a Daytona browser-capable snapshot."
---

# Browser Interaction (Daytona)

Use when plain `fetch` / `urllib` returns empty or incomplete HTML (SPAs, dynamic docs).

## When to use what

| Approach | Use when |
|----------|----------|
| Python `urllib` / `requests` | Static HTML, APIs, raw text downloads |
| `document_text` REPL variable | fleet-rlm already fetched the URL into RLM |
| Playwright (browser snapshot) | Client-rendered pages, need visible text or screenshots |

fleet-rlm selects a **browser-capable Daytona snapshot** when this skill is active.

## Playwright patterns (sandbox)

```python
# After navigation, always print bounded excerpts for the RLM loop.
print(page.title())
print(page.inner_text("body")[:4000])
```

- Wait for network idle or a known selector before extracting text.
- Capture screenshots to the volume `artifacts/` path for evidence.
- Bound extracted text; use `llm_query` on excerpts, not full page dumps.

## Safety

- Do not exfiltrate credentials, cookies, or environment variables.
- Do not automate logins unless the user explicitly provided test credentials in-scope.
- Cap page size and iteration count; prefer section samples over full DOM serializations.

## Volume access

Bundled instructions are seeded to `{volume}/skills/system/browser-interaction.md`.
Call `load_skill("browser-interaction")` from the REPL when the volume is mounted.
