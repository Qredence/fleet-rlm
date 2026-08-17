/**
 * P24/QRE-168+169: the committed cross-language fixtures are adapter-exact on
 * the TS side, and live/durable routes through the SHARED reducer produce the
 * same visible transcript for the same semantic run.
 */

import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { FleetTurn } from "../../fleet-api-client.js";
import type { FleetUIMessageChunk } from "../../sse.js";
import { serializeCanonicalEvent, type CanonicalEvent } from "../canonical.js";
import { adaptDurableTurns } from "../durable-adapter.js";
import { adaptLiveChunk } from "../live-adapter.js";
import { projectDurableTurns } from "../durable-projection.js";
import { LiveTurnProjector } from "../live-projection.js";
import type { Message, StoreEvent } from "../store.js";

interface FixtureEntry {
  scenario_index: number;
  stream_id: string;
  canonical: Record<string, unknown>;
  live_chunk: Record<string, unknown> | null;
  reload_part: Record<string, unknown> | null;
}

const FIXTURE_DIR = resolve(__dirname, "../../../../../tests/fixtures/canonical-events");
const clock = () => 100;

function scenario(name: string): FixtureEntry[] {
  return readFileSync(resolve(FIXTURE_DIR, `${name}.jsonl`), "utf-8")
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as FixtureEntry);
}

const scenarios = readdirSync(FIXTURE_DIR)
  .filter((name) => name.endsWith(".jsonl"))
  .map((name) => name.replace(/\.jsonl$/, ""))
  .sort();

function semanticView(events: StoreEvent[], dropKinds: string[] = []): unknown[] {
  const byId = new Map<string, Message>();
  for (const event of events) {
    if (event.type === "message/upsert") byId.set(event.message.id, event.message);
  }
  for (const kind of dropKinds) {
    for (const [id, message] of byId) if (message.kind === kind) byId.delete(id);
  }
  return [...byId.values()].map((message) => {
    const { id: _id, ts: _ts, ...rest } = message as Message & { runId?: string };
    const { runId: _runId, ...visible } = rest;
    return visible;
  });
}

describe("canonical event fixtures", () => {
  for (const name of scenarios) {
    describe(`scenario: ${name}`, () => {
      const entries = scenario(name);
      it("TS adapters pin identical canonical payloads", () => {
        for (const entry of entries) {
          if (entry.live_chunk !== null) {
            const produced = adaptLiveChunk(entry.live_chunk as unknown as FleetUIMessageChunk).map(
              serializeCanonicalEvent,
            );
            for (const event of produced) {
              expect({ ...event }).toEqual({ ...entry.canonical });
            }
          }
          if (entry.reload_part !== null) {
            const produced = adaptDurableTurns([
              {
                id: "turn-fixture",
                role: "assistant",
                metadata: { runId: "turn-fixture" },
                parts: [entry.reload_part as FleetTurn["parts"][number]],
              },
            ]).filter((event) => event.type === entry.canonical.type);
            // data-step/data-status/step-start are live-only lifecycle pins on
            // reload: adapters suppress them explicitly (never fabricated).
            if (
              ["data-step", "data-status", "step-start"].includes(String(entry.reload_part.type))
            ) {
              expect(produced).toHaveLength(0);
            } else {
              expect(produced.length).toBeGreaterThanOrEqual(1);
              const producedWire = serializeCanonicalEvent(produced[0] as CanonicalEvent);
              // Positional reload minting and reasoning step attribution are
              // turn-context decisions resolved for the reducer (live already
              // infers from stream ids); semantic identity fields are pinned.
              const semantic = (wire: Record<string, unknown>) => {
                const { stream_id: _s, message_id: _m, ...rest } = wire;
                if (rest.type === "reasoning") delete rest.step;
                return rest;
              };
              expect(semantic(producedWire)).toEqual(semantic(entry.canonical));
            }
          }
        }
      });

      it("live and reload routes converge to one visible transcript", () => {
        const live = new LiveTurnProjector(clock);
        let liveEvents: StoreEvent[] = [];
        for (const entry of entries) {
          if (entry.live_chunk !== null) {
            liveEvents = liveEvents.concat(
              live.push(entry.live_chunk as unknown as FleetUIMessageChunk),
            );
          }
        }
        const durableEvents = projectDurableTurns(
          [
            {
              id: "turn-fixture",
              role: "assistant",
              metadata: { runId: "turn-fixture" },
              parts: entries
                .filter((entry) => entry.reload_part !== null)
                .map((entry) => JSON.stringify(entry.reload_part))
                .filter((part, index, all) => all.indexOf(part) === index)
                .map((part) => JSON.parse(part) as FleetTurn["parts"][number]),
            },
          ],
          clock,
        );
        // Live-only edges (stream errors) have no reload representation by
        // design (fixture pins mark their entries with reload_part: null).
        expect(semanticView(durableEvents)).toEqual(semanticView(liveEvents, ["error"]));
      });
    });
  }
});
