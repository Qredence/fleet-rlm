/**
 * Durable Session reload projection (P24): reload parts adapt into the
 * canonical vocabulary and the shared source-agnostic reducer owns all
 * projection state. Kept as the stable `projectDurableTurns` facade for the
 * hydration call sites.
 */

import type { FleetTurn } from "../fleet-api-client.js";
import { adaptDurableTurns } from "./durable-adapter.js";
import type { Clock } from "./projection-helpers.js";
import type { StoreEvent } from "./store.js";
import { TurnEventReducer } from "./turn-reducer.js";

export function projectDurableTurns(turns: FleetTurn[], clock: Clock = Date.now): StoreEvent[] {
  const reducer = new TurnEventReducer(clock);
  const events: StoreEvent[] = [];
  for (const canonical of adaptDurableTurns(turns)) {
    events.push(...reducer.push(canonical));
  }
  return events;
}
