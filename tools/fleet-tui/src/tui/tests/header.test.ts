import { describe, expect, it } from "vitest";

import { ConversationStore } from "../store.js";
import { setTerminalColorScheme } from "../theme.js";
import { FleetScreen } from "../screen.js";
import { stripAnsi } from "./support/ansi.js";

// The header renders session-state-derived content without needing a live TUI.
// We verify it produces the expected styled lines for active/archived states.

async function makeScreen(sessionStatus: "active" | "archived" = "active") {
  const { TuiAltScreen, Editor } = await import("@earendil-works/pi-tui");
  const { FakeTerminal } = await import("./fake-terminal.js");
  const terminal = new FakeTerminal();
  const ui = new TuiAltScreen(terminal, undefined, undefined, { mouse: false });
  const store = new ConversationStore();
  store.dispatch({
    type: "session/init",
    session: { id: "s1", title: "My Session", status: sessionStatus, resumed: false },
  });
  const editor = new Editor(ui, {
    borderColor: (t) => t,
    selectList: {
      selectedPrefix: (t) => t,
      selectedText: (t) => t,
      description: (t) => t,
      scrollInfo: (t) => t,
      noMatch: (t) => t,
    },
  });
  return { store, screen: new FleetScreen(store, editor, terminal, ui), ui };
}

describe("HeaderComponent", () => {
  it("renders session title in header for active session", async () => {
    setTerminalColorScheme("dark");
    const { screen } = await makeScreen("active");
    const lines = screen.render(80);
    const header = lines[0] ?? "";
    expect(header).toContain("My Session");
    expect(header).toContain("active");
  });

  it("renders archived badge for archived session", async () => {
    setTerminalColorScheme("dark");
    const { screen } = await makeScreen("archived");
    const lines = screen.render(80);
    const header = lines[0] ?? "";
    expect(header).toContain("My Session");
    expect(header).toContain("archived");
  });

  it("updates header after session rename", async () => {
    setTerminalColorScheme("dark");
    const { store, screen } = await makeScreen("active");
    const before = screen.render(80)[0] ?? "";
    expect(before).toContain("My Session");

    store.dispatch({
      type: "session/init",
      session: { id: "s1", title: "Renamed Session", status: "active", resumed: false },
    });
    screen.invalidate();
    const after = screen.render(80)[0] ?? "";
    expect(after).toContain("Renamed Session");
    expect(after).not.toContain("My Session");
  });

  it("shows model name when set in store", async () => {
    setTerminalColorScheme("dark");
    const { store, screen } = await makeScreen("active");
    store.dispatch({ type: "settings/model", model: "gpt-4o" });
    screen.invalidate();
    const header = screen.render(80)[0] ?? "";
    expect(header).toContain("gpt-4o");
  });

  it("draws a full-width rule beneath the identity line", async () => {
    setTerminalColorScheme("dark");
    const { screen } = await makeScreen("active");
    const rule = stripAnsi(screen.render(80)[1] ?? "");
    expect(rule.replace(/\s/g, "").length).toBe(80);
  });
});
