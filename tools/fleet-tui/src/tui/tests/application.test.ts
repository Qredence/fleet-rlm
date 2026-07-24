import type { Terminal } from "@earendil-works/pi-tui";
import { describe, expect, it, vi } from "vitest";

import { FleetApiClient } from "../../fleet-api-client.js";
import { createFleetTui } from "../application.js";
import type { StoreEvent } from "../store.js";
import { getTerminalColorScheme, setTerminalColorScheme, theme } from "../theme.js";

class FakeTerminal implements Terminal {
  columns = 80;
  rows = 24;
  kittyProtocolActive = false;
  writes: string[] = [];
  progress: boolean[] = [];
  private onInput?: (data: string) => void;
  private onResize?: () => void;

  start(onInput: (data: string) => void, onResize: () => void): void {
    this.onInput = onInput;
    this.onResize = onResize;
  }
  stop(): void {}
  async drainInput(): Promise<void> {}
  write(data: string): void {
    this.writes.push(data);
  }
  moveBy(): void {}
  hideCursor(): void {}
  showCursor(): void {}
  clearLine(): void {}
  clearFromCursor(): void {}
  clearScreen(): void {}
  setTitle(): void {}
  setProgress(active: boolean): void {
    this.progress.push(active);
  }
  send(data: string): void {
    this.onInput?.(data);
  }
  resize(columns: number, rows: number): void {
    this.columns = columns;
    this.rows = rows;
    this.onResize?.();
  }
}

const session = {
  id: "00000000-0000-4000-8000-000000000001",
  title: "Test",
  status: "active" as const,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  checkpoint_version: 0,
};

describe("FleetTuiApplication", () => {
  it("starts dark and rerenders when the terminal reports the light scheme", async () => {
    setTerminalColorScheme("light");
    const terminal = new FakeTerminal();
    const app = createFleetTui({
      terminal,
      client: new FleetApiClient({ baseUrl: "http://fleet.test" }),
      session,
      resumed: false,
      initialEvents: [],
    });

    const finished = app.start();
    expect(getTerminalColorScheme()).toBe("dark");
    await vi.waitFor(() => expect(terminal.writes).toContain("\x1b[?996n"));
    const writesBeforeSchemeChange = terminal.writes.length;

    terminal.send("\x1b[?997;2n");

    await vi.waitFor(() => expect(getTerminalColorScheme()).toBe("light"));
    await vi.waitFor(() =>
      expect(terminal.writes.length).toBeGreaterThan(writesBeforeSchemeChange),
    );
    await app.stop();
    await finished;
  });

  it("renders one flat native-scrollback transcript and restores terminal state on exit", async () => {
    const terminal = new FakeTerminal();
    const initialEvents: StoreEvent[] = [
      {
        type: "message/upsert",
        message: {
          id: "reason",
          kind: "reasoning",
          runId: "run",
          step: 1,
          text: "visible historical evidence",
          ts: 1,
        },
      },
    ];
    const app = createFleetTui({
      terminal,
      client: new FleetApiClient({ baseUrl: "http://fleet.test" }),
      session,
      resumed: true,
      initialEvents,
      queryColorScheme: false,
    });

    const finished = app.start();
    await vi.waitFor(() =>
      expect(terminal.writes.join("")).toContain("visible historical evidence"),
    );
    expect(terminal.writes.join("")).toContain("FLEET");
    for (const mode of [1000, 1002, 1003, 1006]) {
      expect(terminal.writes.join("")).not.toContain(`${String.fromCharCode(27)}[?${mode}h`);
    }

    terminal.send("\x04");
    await finished;
    expect(terminal.progress.at(-1)).toBe(false);
  });

  it("renders all 10,000 retained rows instead of clipping to terminal height", async () => {
    const terminal = new FakeTerminal();
    const body = Array.from({ length: 10_000 }, (_, index) => `row-${index}`).join("\n");
    const app = createFleetTui({
      terminal,
      client: new FleetApiClient({ baseUrl: "http://fleet.test" }),
      session,
      resumed: false,
      initialEvents: [
        {
          type: "message/upsert",
          message: { id: "output", kind: "output", runId: "run", step: 1, output: body, ts: 1 },
        },
      ],
      queryColorScheme: false,
    });

    const finished = app.start();
    await vi.waitFor(() => expect(terminal.writes.join("")).toContain("row-9999"), {
      timeout: 2_000,
    });
    expect(terminal.writes.join("")).toContain("row-0");
    await app.stop();
    await finished;
  });

  it("shows a live loading action for the currently running Tool", async () => {
    const terminal = new FakeTerminal();
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });
    const encoder = new TextEncoder();
    client.streamTurn = vi.fn(({ signal }) =>
      Promise.resolve(
        new Response(
          new ReadableStream<Uint8Array>({
            start(stream) {
              stream.enqueue(
                encoder.encode(
                  'data: {"type":"start","messageId":"run-loader","messageMetadata":{"delivery":"live"}}\n\n' +
                    'data: {"type":"tool-input-available","toolCallId":"call-loader","toolName":"inspect_workspace","input":{}}\n\n',
                ),
              );
              signal?.addEventListener("abort", () =>
                stream.error(new DOMException("aborted", "AbortError")),
              );
            },
          }),
          { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
        ),
      ),
    );
    client.requestCancellation = vi.fn().mockResolvedValue({ status: "cancelled" });
    const app = createFleetTui({
      terminal,
      client,
      session,
      resumed: false,
      initialEvents: [],
      queryColorScheme: false,
    });

    const finished = app.start();
    for (const key of "inspect") terminal.send(key);
    terminal.send("\r");

    await vi.waitFor(() =>
      expect(terminal.writes.join("")).toContain("Running Tool inspect_workspace"),
    );
    expect(
      ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"].some((frame) =>
        terminal.writes.join("").includes(theme.fg("accent", frame)),
      ),
    ).toBe(true);
    terminal.send("\x1b");
    await vi.waitFor(() => expect(client.requestCancellation).toHaveBeenCalledWith("run-loader"));
    terminal.send("\x04");
    await finished;
  });

  it("keeps an unsent draft during a Run and routes Escape to cancellation", async () => {
    const terminal = new FakeTerminal();
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });
    const encoder = new TextEncoder();
    client.streamTurn = vi.fn(({ signal }) =>
      Promise.resolve(
        new Response(
          new ReadableStream<Uint8Array>({
            start(stream) {
              stream.enqueue(
                encoder.encode(
                  'data: {"type":"start","messageId":"run-1","messageMetadata":{"delivery":"live"}}\n\n',
                ),
              );
              signal?.addEventListener("abort", () =>
                stream.error(new DOMException("aborted", "AbortError")),
              );
            },
          }),
          { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
        ),
      ),
    );
    client.requestCancellation = vi.fn().mockResolvedValue({ status: "cancelled" });
    const app = createFleetTui({
      terminal,
      client,
      session,
      resumed: false,
      initialEvents: [],
      queryColorScheme: false,
    });

    const finished = app.start();
    for (const key of "first") terminal.send(key);
    terminal.send("\r");
    await vi.waitFor(() => expect(client.streamTurn).toHaveBeenCalled());

    for (const key of "next draft") terminal.send(key);
    await vi.waitFor(() => expect(terminal.writes.join("")).toContain("next draft"));

    terminal.send("\x1b");
    await vi.waitFor(() => expect(client.requestCancellation).toHaveBeenCalledWith("run-1"));
    await vi.waitFor(() => expect(terminal.progress.at(-1)).toBe(false));
    expect(terminal.writes.join("")).toContain("next draft");

    terminal.send("\x03");
    terminal.send("\x04");
    await finished;
  });
});
