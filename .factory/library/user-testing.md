# User Testing

## Validation Surface
Backend-only. No browser/UI testing needed.

**Primary validation:** `make test` (pytest excluding live_llm and benchmark)
**Secondary:** `make lint`, `make typecheck`

## Validation Concurrency
Not applicable — no concurrent browser/CLI validators needed.
All validation is via pytest which handles its own parallelism.

## Testing Tools
- pytest with pytest-asyncio
- rg (ripgrep) for import checking
- make commands for lint/typecheck
