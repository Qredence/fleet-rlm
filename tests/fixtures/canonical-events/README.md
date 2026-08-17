# Canonical semantic event fixtures (P24/QRE-168)

One JSONL file per scenario. Each line is one wire item:

```json
{
  "scenario_index": 0,
  "canonical": {"type": "...", ...},
  "live_chunk": {...} | null,
  "reload_part": {...} | null,
  "stream_id": "synthetic stream id used for reload synthesis"
}
```

- `canonical` is the exact post-adapter JSON (snake_case, top-level `None`s omitted).
- `live_chunk` is a strict `FleetUIMessageChunk` wire item (live SSE); `null` for
  parts that only exist durably.
- `reload_part` is a durable `UIMessagePart` wire item; `null` for live-only
  lifecycle edges (turn start/finish/status).
- Both adapters must agree on `canonical`; the TUI reducer must derive the same
  semantic transcript from either route (asserted message-level, not id-level).

Durable reload carries no Run lifecycle/status edges — those entries pin the
live-only shapes so adapters stay explicit about routing.
