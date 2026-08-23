import { Editor, TuiAltScreen } from "@earendil-works/pi-tui";
import { describe, expect, it } from "vitest";

import { FleetScreen } from "../screen.js";
import { ConversationStore, type StoreEvent } from "../store.js";
import { theme } from "../theme.js";
import { FakeTerminal } from "./fake-terminal.js";

function needleOutput(): StoreEvent {
  return {
    type: "message/upsert",
    message: {
      id: "out",
      kind: "output",
      runId: "r",
      step: 1,
      output: "needle alpha\nneedle beta\nnothing here",
      ts: 1,
    },
  };
}

function createStore(): ConversationStore {
  const store = new ConversationStore();
  store.dispatch({
    type: "session/init",
    session: { id: "s", title: "T", status: "active", resumed: false },
  });
  store.dispatch(needleOutput());
  return store;
}

function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 60));
}

describe("transcript search (pi-tui 0.84.2)", () => {
  it("opens the search overlay on Ctrl+Shift+F and closes it with Escape", async () => {
    const terminal = new FakeTerminal();
    const ui = new TuiAltScreen(terminal, undefined, undefined, { mouse: true });
    const store = createStore();
    const screen = new FleetScreen(
      store,
      new Editor(ui, {
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
    await tick();

    expect(ui.hasOverlay()).toBe(false);
    terminal.send("\x1b[102;6u"); // Ctrl+Shift+F (kitty CSI-u)
    await tick();
    expect(ui.hasOverlay()).toBe(true);

    terminal.send("\x1b"); // Escape closes the search overlay
    await tick();
    expect(ui.hasOverlay()).toBe(false);

    ui.stop();
  });

  it("highlights matches with the Fleet theme search styles", async () => {
    const terminal = new FakeTerminal();
    const ui = new TuiAltScreen(terminal, undefined, undefined, {
      mouse: true,
      // Sentinel wrappers prove the option wiring; token fidelity is covered
      // by the theme tests.
      searchMatchStyle: (text) => `<${text}>`,
      searchCurrentMatchStyle: (text) => `[${text}]`,
    });
    const store = createStore();
    const screen = new FleetScreen(
      store,
      new Editor(ui, {
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
    await tick();

    terminal.send("\x1b[102;6u");
    await tick();
    for (const ch of "needle") terminal.send(ch);
    await tick();

    const rendered = terminal.writes.join("");
    expect(rendered).toContain("[needle]");
    expect(rendered).toContain("<needle>");

    ui.stop();
  });

  it("resolves the Fleet theme search styles the application wires in", () => {
    expect(theme.searchMatch()("hit")).toBe("\x1b[4mhit\x1b[24m");
    expect(theme.currentSearchMatch()("hit")).toContain("\x1b[48;");
  });
});
