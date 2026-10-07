import { visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it, vi } from "vitest";

import { type FleetApiClient, FleetApiError, type FleetSession } from "../../fleet-api-client.js";
import { SessionBrowserOverlay } from "../presenter/session-browser.js";
import { ConversationStore } from "../store.js";
import { setTerminalColorScheme } from "../theme.js";
import { stripAnsi } from "./support/ansi.js";

function session(
  id: string,
  title: string,
  status: FleetSession["status"] = "active",
): FleetSession {
  return {
    id,
    title,
    status,
    checkpoint_version: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function client(updateSession = vi.fn()): FleetApiClient {
  return { updateSession } as unknown as FleetApiClient;
}

describe("SessionBrowserOverlay", () => {
  it("renders a columned list with the shared key footer and width bound", () => {
    setTerminalColorScheme("dark");
    const overlay = new SessionBrowserOverlay(
      [session("a", "Investigate latency"), session("b", "Draft report", "archived")],
      client(),
      vi.fn(),
      vi.fn(),
    );

    const lines = overlay.render(72);
    const output = lines.map(stripAnsi).join("\n");

    expect(output).toContain("Sessions");
    expect(output).toContain("Investigate latency");
    expect(output).toContain("archived");
    expect(output).toContain("ENTER resume");
    expect(lines.every((line) => visibleWidth(line) <= 72)).toBe(true);
  });

  it("filters the list as the user types", () => {
    setTerminalColorScheme("dark");
    const overlay = new SessionBrowserOverlay(
      [session("a", "Investigate latency"), session("b", "Draft report")],
      client(),
      vi.fn(),
      vi.fn(),
    );

    for (const key of "draft") overlay.handleInput(key);
    const output = overlay.render(72).map(stripAnsi).join("\n");

    expect(output).toContain("Draft report");
    expect(output).not.toContain("Investigate latency");
  });

  it("resumes the selected session on Enter", () => {
    setTerminalColorScheme("dark");
    const finish = vi.fn();
    const overlay = new SessionBrowserOverlay([session("a", "One")], client(), finish, vi.fn());

    overlay.handleInput("\r");
    expect(finish).toHaveBeenCalledWith({ action: "resume", id: "a" });
  });

  it("keeps bare letters available for filtering (actions use Ctrl)", async () => {
    setTerminalColorScheme("dark");
    const updateSession = vi.fn().mockResolvedValue(session("a", "Aria report"));
    const overlay = new SessionBrowserOverlay(
      [session("a", "Aria report"), session("b", "Beta")],
      client(updateSession),
      vi.fn(),
      vi.fn(),
    );

    // "ar" contains the letters bound to the rename/archive actions; typing it
    // must filter, not trigger an action.
    for (const key of "ar") overlay.handleInput(key);
    const output = overlay.render(72).map(stripAnsi).join("\n");
    expect(output).toContain("Aria report");
    expect(output).not.toContain("Beta");
    expect(updateSession).not.toHaveBeenCalled();

    // Ctrl+A is the archive action for the selected row.
    overlay.handleInput("\x01");
    await vi.waitFor(() => expect(updateSession).toHaveBeenCalled());
    expect(updateSession.mock.calls[0]?.[1]).toEqual({ status: "archived" });
  });

  it("publishes successful archive and unarchive updates", async () => {
    const updateSession = vi
      .fn()
      .mockResolvedValueOnce(session("a", "One", "archived"))
      .mockResolvedValueOnce(session("a", "One", "active"));
    const updated = vi.fn();
    const overlay = new SessionBrowserOverlay(
      [session("a", "One")],
      client(updateSession),
      vi.fn(),
      vi.fn(),
      1,
      updated,
    );
    overlay.handleInput("\x01");
    await vi.waitFor(() => expect(updated).toHaveBeenCalledTimes(1));
    expect(updated).toHaveBeenLastCalledWith(session("a", "One", "archived"));
    overlay.handleInput("\x01");
    await vi.waitFor(() => expect(updated).toHaveBeenCalledTimes(2));
    expect(updateSession).toHaveBeenLastCalledWith("a", { status: "active" });
  });

  it("publishes a committed archive when provider retirement is pending", async () => {
    const current = session("a", "One");
    const store = new ConversationStore();
    store.dispatch({
      type: "session/init",
      session: { id: current.id, title: current.title, status: current.status, resumed: true },
    });
    const onSessionUpdated = vi.fn((updated: FleetSession) => {
      store.dispatch({
        type: "session/init",
        session: {
          id: updated.id,
          title: updated.title,
          status: updated.status,
          resumed: true,
        },
      });
    });
    const finish = vi.fn();
    const updateSession = vi
      .fn()
      .mockRejectedValue(
        new FleetApiError(
          503,
          "Session retirement is pending",
          undefined,
          "session_retirement_pending",
        ),
      );
    const overlay = new SessionBrowserOverlay(
      [current],
      client(updateSession),
      finish,
      vi.fn(),
      1,
      onSessionUpdated,
    );

    overlay.handleInput("\x01");
    await vi.waitFor(() =>
      expect(stripAnsi(overlay.render(72).join("\n"))).toContain(
        "Session archived, but provider retirement is pending.",
      ),
    );

    expect(stripAnsi(overlay.render(72).join("\n"))).toContain("archived");
    expect(onSessionUpdated).toHaveBeenCalledWith({ ...current, status: "archived" });
    expect(store.getState().session?.status).toBe("archived");
    expect(finish).not.toHaveBeenCalled();
  });

  it("keeps an archived row unchanged when unarchive fails", async () => {
    const archived = session("a", "One", "archived");
    const updated = vi.fn();
    const overlay = new SessionBrowserOverlay(
      [archived],
      client(vi.fn().mockRejectedValue(new Error("offline"))),
      vi.fn(),
      vi.fn(),
      1,
      updated,
    );

    overlay.handleInput("\x01");
    await vi.waitFor(() =>
      expect(stripAnsi(overlay.render(72).join("\n"))).toContain("Failed to unarchive session."),
    );

    expect(stripAnsi(overlay.render(72).join("\n"))).toContain("archived");
    expect(updated).not.toHaveBeenCalled();
  });

  it("publishes a successful rename", async () => {
    const updated = vi.fn();
    const updateSession = vi.fn().mockResolvedValue(session("a", "One more"));
    const overlay = new SessionBrowserOverlay(
      [session("a", "One")],
      client(updateSession),
      vi.fn(),
      vi.fn(),
      1,
      updated,
    );
    overlay.handleInput("\x12");
    overlay.handleInput(" more");
    overlay.handleInput("\r");
    await vi.waitFor(() => expect(updated).toHaveBeenCalledWith(session("a", "One more")));
    expect(updateSession).toHaveBeenCalledWith("a", { title: "One more" });
  });

  it("keeps failed updates local and retains rename text for retry", async () => {
    const updated = vi.fn();
    const overlay = new SessionBrowserOverlay(
      [session("a", "One")],
      client(vi.fn().mockRejectedValue(new Error("offline"))),
      vi.fn(),
      vi.fn(),
      1,
      updated,
    );
    overlay.handleInput("\x01");
    await vi.waitFor(() =>
      expect(stripAnsi(overlay.render(72).join("\n"))).toContain("Failed to archive"),
    );
    overlay.handleInput("\x12");
    overlay.handleInput(" more");
    overlay.handleInput("\r");
    await vi.waitFor(() =>
      expect(stripAnsi(overlay.render(72).join("\n"))).toContain("Rename failed"),
    );
    expect(stripAnsi(overlay.render(72).join("\n"))).toContain("One more");
    expect(updated).not.toHaveBeenCalled();
  });

  it("sanitizes a backend session title in the rename editor", () => {
    setTerminalColorScheme("dark");
    const hostile = session("a", "Evil\x1b]52;c;secret\x07\x1b[2JTitle");
    const overlay = new SessionBrowserOverlay([hostile], client(), vi.fn(), vi.fn());

    overlay.handleInput("\x12"); // Ctrl+R opens rename seeded from the title
    const output = overlay.render(72);

    for (const line of output) {
      expect(line).not.toContain("\x07");
      expect(line).not.toContain("\x1b]52");
      expect(line).not.toContain("\x1b[2J");
    }
    expect(output.map(stripAnsi).join("\n")).toContain("Title");
    expect(output.every((line) => visibleWidth(line) <= 72)).toBe(true);
  });
});
