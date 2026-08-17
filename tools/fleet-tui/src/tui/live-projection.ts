/**
 * Live SSE turn projection (P24): strict lifecycle stays at the transport
 * boundary; every chunk adapts into the canonical vocabulary and the shared
 * source-agnostic reducer owns all projection state. Kept as the stable
 * `LiveTurnProjector` facade for the runner call site.
 */

import type { FleetUIMessageChunk } from "../sse.js";
import { adaptLiveChunk } from "./live-adapter.js";
import type { Clock } from "./projection-helpers.js";
import type { StoreEvent } from "./store.js";
import { TurnEventReducer } from "./turn-reducer.js";

export class LiveTurnProjector {
  private readonly reducer: TurnEventReducer;

  constructor(clock: Clock = Date.now) {
    this.reducer = new TurnEventReducer(clock);
  }

  push(chunk: FleetUIMessageChunk): StoreEvent[] {
    const events: StoreEvent[] = [];
    for (const canonical of adaptLiveChunk(chunk)) {
      events.push(...this.reducer.push(canonical));
    }
    return events;
  }
}
