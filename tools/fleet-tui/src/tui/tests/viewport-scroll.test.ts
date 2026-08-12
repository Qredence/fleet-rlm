import { TuiAltScreen, type Terminal } from "@earendil-works/pi-tui";
import { describe, expect, it } from "vitest";

import { createFleetTui } from "../application.js";
import { FleetApiClient } from "../../fleet-api-client.js";
import { ConversationStore, type StoreEvent } from "../store.js";
import { FleetScreen } from "../screen.js";

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

function longOutput(): StoreEvent {
  return {
    type: "message/upsert",
    message: {
      id: "out",
      kind: "output",
      runId: "r",
      step: 1,
      output: Array.from({ length: 500 }, (_, index) => `row-${index}`).join("\n"),
      ts: 1,
    },
  };
}

describe("transcript viewport scrolling", () => {
  it("scrolls the transcript with PageUp/PageDown and re-follows the end", async () => {
    const terminal = new FakeTerminal();
    const ui = new TuiAltScreen(terminal, undefined, undefined, { mouse: true });
    const store = new ConversationStore();
    store.dispatch({
      type: "session/init",
      session: { id: "s", title: "T", status: "active", resumed: false },
    });
    store.dispatch(longOutput());
    const screen = new FleetScreen(
      store,
      new (await import("@earendil-works/pi-tui")).Editor(ui, {
        borderColor: (t) => t,
        selectList: {
          selectedPrefix: (t) => t,
          selectedText: (t) => t,
          description: (t) => t,
          scrollInfo: (t) => t,
          noMatch: (t) => t,
        },
      }),
      terminal,
      ui,
    );
    ui.setLayoutRoot(screen);
    ui.start();
    await new Promise((resolve) => setTimeout(resolve, 100));

    const before = ui.viewportTop;
    expect(before).toBeGreaterThan(0);

    terminal.send("\x1b[5~"); // PageUp
    await new Promise((resolve) => setTimeout(resolve, 50));
    const afterPageUp = ui.viewportTop;
    expect(afterPageUp).toBeLessThan(before);

    terminal.send("\x1b[6~"); // PageDown
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(ui.viewportTop).toBeGreaterThan(afterPageUp);

    terminal.send("\x1b[6~");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(ui.isFollowingOutput).toBe(true);

    ui.stop();
  });

  it("scrolls with the mouse wheel and stays keyboard-usable", async () => {
    const terminal = new FakeTerminal();
    const ui = new TuiAltScreen(terminal, undefined, undefined, { mouse: true });
    const store = new ConversationStore();
    store.dispatch({
      type: "session/init",
      session: { id: "s", title: "T", status: "active", resumed: false },
    });
    store.dispatch(longOutput());
    const screen = new FleetScreen(
      store,
      new (await import("@earendil-works/pi-tui")).Editor(ui, {
        borderColor: (t) => t,
        selectList: {
          selectedPrefix: (t) => t,
          selectedText: (t) => t,
          description: (t) => t,
          scrollInfo: (t) => t,
          noMatch: (t) => t,
        },
      }),
      terminal,
      ui,
    );
    ui.setLayoutRoot(screen);
    ui.start();
    await new Promise((resolve) => setTimeout(resolve, 100));

    const before = ui.viewportTop;
    terminal.send("\x1b[<64;40;12M"); // wheel up
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(ui.viewportTop).toBeLessThan(before);

    terminal.send("\x1b[<65;40;12M"); // wheel down
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(ui.viewportTop).toBeGreaterThanOrEqual(before - 1);

    ui.stop();
  });

  it("enables SGR mouse modes in the real application so wheels scroll", async () => {
    const terminal = new FakeTerminal();
    const app = createFleetTui({
      terminal,
      client: new FleetApiClient({ baseUrl: "http://fleet.test" }),
      session,
      resumed: false,
      initialEvents: [longOutput()],
      queryColorScheme: false,
    });
    const finished = app.start();
    await new Promise((resolve) => setTimeout(resolve, 150));
    const writes = terminal.writes.join("");
    // Wheel + selection require the SGR mouse modes; button motion enables drag-copy.
    expect(writes).toContain("\x1b[?1000h");
    expect(writes).toContain("\x1b[?1006h");
    await app.stop();
    await finished;
  });
});
