import { describe, expect, it, vi } from "vitest";

import type { FleetApiClient } from "../fleet-api-client.js";
import { ConversationStore } from "./store.js";
import { listCommands, parseInput, type CommandContext } from "./commands.js";

function makeContext(): { ctx: CommandContext; exits: { count: number } } {
  const store = new ConversationStore();
  const client = {} as FleetApiClient;
  let count = 0;
  const ctx: CommandContext = {
    store,
    client,
    cancelActiveRun: () => undefined,
    exit: () => {
      count += 1;
    },
  };
  return {
    ctx,
    exits: {
      get count() {
        return count;
      },
      set count(v) {
        count = v;
      },
    } as { count: number },
  };
}

describe("parseInput", () => {
  it("returns empty for blank input", () => {
    expect(parseInput("   ")).toEqual({ kind: "empty" });
  });

  it("passes non-slash input through as a message", () => {
    expect(parseInput("hello world")).toEqual({ kind: "message", text: "hello world" });
  });

  it("matches a registered slash command", () => {
    const result = parseInput("/help");
    expect(result.kind).toBe("command");
    if (result.kind === "command") {
      expect(result.spec.name).toBe("help");
      expect(result.args).toEqual([]);
    }
  });

  it("falls back to a message for unknown commands (preserves /foo text)", () => {
    expect(parseInput("/unknown-cmd")).toEqual({ kind: "message", text: "/unknown-cmd" });
  });
});

describe("command handlers", () => {
  it("/help appends a system message and lists commands", async () => {
    const { ctx } = makeContext();
    const help = listCommands().find((c) => c.name === "help");
    expect(help).toBeDefined();
    if (help) await help.handler([], ctx);
    const sys = ctx.store.getState().messages.find((m) => m.kind === "text" && m.role === "system");
    expect(sys?.kind).toBe("text");
    if (sys?.kind === "text") {
      expect(sys.text).toContain("/help");
      expect(sys.text).toContain("/cancel");
      expect(sys.text).toContain("/exit");
    }
  });

  it("/clear wipes messages but keeps the session", () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "s", title: "t", status: "active", resumed: false },
    });
    ctx.store.dispatch({ type: "user/submit", text: "hi" });
    expect(ctx.store.getState().messages.length).toBeGreaterThan(0);
    const clear = listCommands().find((c) => c.name === "clear");
    if (clear) clear.handler([], ctx);
    expect(ctx.store.getState().messages).toHaveLength(1);
    expect(ctx.store.getState().session?.id).toBe("s");
  });

  it("/exit calls the exit handler", () => {
    const { ctx, exits } = makeContext();
    const exit = listCommands().find((c) => c.name === "exit");
    if (exit) exit.handler([], ctx);
    expect(exits.count).toBe(1);
  });

  it("/status renders a system message including session id", () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "abc-123", title: "T", status: "active", resumed: false },
    });
    const status = listCommands().find((c) => c.name === "status");
    if (status) status.handler([], ctx);
    const sys = ctx.store.getState().messages.find((m) => m.kind === "text" && m.role === "system");
    expect(sys?.kind).toBe("text");
    if (sys?.kind === "text") {
      expect(sys.text).toContain("abc-123");
    }
  });

  it("/cancel forwards to the cancelActiveRun hook", () => {
    const { ctx } = makeContext();
    const cancelSpy = vi.fn();
    ctx.cancelActiveRun = cancelSpy;
    const cancel = listCommands().find((c) => c.name === "cancel");
    if (cancel) cancel.handler([], ctx);
    expect(cancelSpy).toHaveBeenCalledTimes(1);
  });
});
