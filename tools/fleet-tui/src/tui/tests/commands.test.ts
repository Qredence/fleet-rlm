import { describe, expect, it, vi } from "vitest";

import type { FleetApiClient } from "../../fleet-api-client.js";
import { ConversationStore } from "../store.js";
import { formatVolumeTree, listCommands, parseInput, type CommandContext } from "../commands.js";

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

  it("rejects unknown commands locally", () => {
    expect(parseInput("/unknown-cmd")).toEqual({ kind: "unknown-command", name: "unknown-cmd" });
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

  it("/volume renders the logical Workspace Volume tree", async () => {
    const { ctx } = makeContext();
    ctx.client.listVolumeTree = vi.fn().mockResolvedValue({
      paths: ["files/notes.md", "sessions/abc/turn.json"],
      directories: ["files", "sessions"],
      truncated: false,
    });
    const volume = listCommands().find((command) => command.name === "volume");
    if (volume) await volume.handler([], ctx);
    const sys = ctx.store.getState().messages.find((m) => m.kind === "text" && m.role === "system");
    expect(sys?.kind).toBe("text");
    if (sys?.kind === "text") expect(sys.text).toContain("sessions");
  });

  it("formats nested volume paths as a tree", () => {
    expect(formatVolumeTree(["files/notes.md", "files/sub/todo.md"])).toContain("sub");
  });

  it("/rename updates the durable and local Session title", async () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "abc-123", title: "Old", status: "active", resumed: true },
    });
    ctx.client.updateSession = vi.fn().mockResolvedValue({
      id: "abc-123",
      title: "Research notes",
      status: "active",
      checkpoint_version: 1,
      created_at: null,
      updated_at: null,
    });

    const rename = listCommands().find((command) => command.name === "rename");
    if (rename) await rename.handler(["Research", "notes"], ctx);

    expect(ctx.client.updateSession).toHaveBeenCalledWith("abc-123", {
      title: "Research notes",
    });
    expect(ctx.store.getState().session).toMatchObject({
      title: "Research notes",
      resumed: true,
    });
  });

  it("/rename does not restore a Session that changed while the request was pending", async () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "old-session", title: "Old", status: "active", resumed: true },
    });
    type UpdatedSession = Awaited<ReturnType<FleetApiClient["updateSession"]>>;
    let resolveUpdate!: (session: UpdatedSession) => void;
    ctx.client.updateSession = vi.fn(
      () =>
        new Promise<UpdatedSession>((resolve) => {
          resolveUpdate = resolve;
        }),
    );

    const rename = listCommands().find((command) => command.name === "rename");
    const pendingRename = rename?.handler(["Renamed"], ctx);
    await vi.waitFor(() => expect(ctx.client.updateSession).toHaveBeenCalled());
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "new-session", title: "New", status: "active", resumed: true },
    });
    resolveUpdate({
      id: "old-session",
      title: "Renamed",
      status: "active",
      checkpoint_version: 1,
    });
    await pendingRename;

    expect(ctx.store.getState().session).toMatchObject({
      id: "new-session",
      title: "New",
    });
  });

  it("/sessions searches active Session titles", async () => {
    const { ctx } = makeContext();
    ctx.client.listSessions = vi.fn().mockResolvedValue({
      items: [],
      total: 0,
      offset: 0,
      limit: 100,
      has_more: false,
    });

    const sessions = listCommands().find((command) => command.name === "sessions");
    if (sessions) await sessions.handler(["research", "notes"], ctx);

    expect(ctx.client.listSessions).toHaveBeenCalledWith({
      limit: 100,
      status: "active",
      search: "research notes",
    });
  });

  it("/cancel forwards to the cancelActiveRun hook", () => {
    const { ctx } = makeContext();
    const cancelSpy = vi.fn();
    ctx.cancelActiveRun = cancelSpy;
    const cancel = listCommands().find((c) => c.name === "cancel");
    if (cancel) cancel.handler([], ctx);
    expect(cancelSpy).toHaveBeenCalledTimes(1);
  });

  it("/skills lists only cards returned by discovery", async () => {
    const { ctx } = makeContext();
    ctx.client.listSkills = vi.fn().mockResolvedValue([
      {
        id: "00000000-0000-4000-8000-000000000001",
        name: "long-context",
        description: "Process inputs that exceed the context window.",
        scope: "system",
        version: "2.0.0",
        trust: "system",
        affordances: [],
        resources_available: true,
      },
    ]);

    const skills = listCommands().find((command) => command.name === "skills");
    if (skills) await skills.handler([], ctx);

    const message = ctx.store.getState().messages.at(-1);
    expect(message).toMatchObject({ kind: "text", role: "system" });
    if (message?.kind === "text") {
      expect(message.text).toContain("long-context@2.0.0");
      expect(message.text).toContain("Process inputs that exceed the context window.");
    }
  });

  it("/skill resolves a visible name and pins its current version", async () => {
    const { ctx } = makeContext();
    ctx.client.listSkills = vi.fn().mockResolvedValue([
      {
        id: "00000000-0000-4000-8000-000000000001",
        name: "long-context",
        description: "Long context",
        scope: "system",
        version: "2.0.0",
        trust: "system",
        affordances: [],
        resources_available: true,
      },
    ]);

    const skill = listCommands().find((command) => command.name === "skill");
    if (skill) await skill.handler(["long-context"], ctx);

    expect(ctx.store.getState().pendingSkillSelections).toEqual([
      {
        id: "00000000-0000-4000-8000-000000000001",
        expectedVersion: "2.0.0",
        displayName: "long-context",
      },
    ]);
  });

  it("/skill accepts an exact hidden UUID and version without discovery", async () => {
    const { ctx } = makeContext();
    ctx.client.listSkills = vi.fn();
    const skill = listCommands().find((command) => command.name === "skill");

    if (skill) {
      await skill.handler(["00000000-0000-4000-8000-000000000099@1.4.0"], ctx);
    }

    expect(ctx.client.listSkills).not.toHaveBeenCalled();
    expect(ctx.store.getState().pendingSkillSelections).toMatchObject([
      {
        id: "00000000-0000-4000-8000-000000000099",
        expectedVersion: "1.4.0",
      },
    ]);
  });

  it("/skill enforces four unique selections and /skill clear removes them", async () => {
    const { ctx } = makeContext();
    const skill = listCommands().find((command) => command.name === "skill");
    for (let index = 1; index <= 5; index += 1) {
      const suffix = String(index).padStart(12, "0");
      if (skill) {
        await skill.handler([`00000000-0000-4000-8000-${suffix}@1.0.0`], ctx);
      }
    }

    expect(ctx.store.getState().pendingSkillSelections).toHaveLength(4);
    const limitMessage = ctx.store.getState().messages.at(-1);
    expect(limitMessage).toMatchObject({ kind: "text", role: "system" });
    if (limitMessage?.kind === "text") {
      expect(limitMessage.text).toContain("At most four unique Skills");
    }

    if (skill) await skill.handler(["clear"], ctx);
    expect(ctx.store.getState().pendingSkillSelections).toEqual([]);
  });

  function makePolicy(
    overrides: Partial<import("../../fleet-api-client.js").FleetSettingsPolicy> = {},
  ) {
    return {
      revision: "a".repeat(64),
      active_profile: "daytona",
      default_profile: "daytona",
      available_profiles: ["daytona", "local-deno", "daytona-bench"],
      restart_required: true,
      scopes: [],
      ...overrides,
    };
  }

  it("/profiles prints a read-only list without a presenter", async () => {
    const { ctx } = makeContext();
    ctx.client.getSettings = vi.fn().mockResolvedValue(makePolicy());
    const profiles = listCommands().find((c) => c.name === "profiles");
    expect(profiles).toBeDefined();
    if (profiles) await profiles.handler([], ctx);
    const sys = ctx.store.getState().messages.find((m) => m.kind === "text" && m.role === "system");
    expect(sys?.kind).toBe("text");
    if (sys?.kind === "text") {
      expect(sys.text).toContain("local-deno");
      expect(sys.text).toContain("current: daytona");
      expect(sys.text).toContain("daytona (current)");
    }
  });

  it("/profiles distinguishes the running and restart-selected profiles", async () => {
    const { ctx } = makeContext();
    ctx.client.getSettings = vi
      .fn()
      .mockResolvedValue(makePolicy({ active_profile: "daytona", default_profile: "local-deno" }));
    const profiles = listCommands().find((c) => c.name === "profiles");
    if (profiles) await profiles.handler([], ctx);

    const message = ctx.store.getState().messages.at(-1);
    expect(message).toMatchObject({ kind: "text", role: "system" });
    if (message?.kind === "text") {
      expect(message.text).toContain("running: daytona; selected: local-deno");
      expect(message.text).toContain("daytona (running)");
      expect(message.text).toContain("local-deno (selected)");
    }
  });

  it("/profiles opens the profile picker and PATCHes on selection", async () => {
    const { ctx } = makeContext();
    const policy = makePolicy();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi
      .fn()
      .mockResolvedValue(makePolicy({ default_profile: "local-deno" }));
    const chooseProfile = vi.fn().mockResolvedValue("local-deno");
    const ctxWithPresenter: CommandContext = { ...ctx, presenter: { chooseProfile } as never };
    const profiles = listCommands().find((c) => c.name === "profiles");
    if (profiles) await profiles.handler([], ctxWithPresenter);
    expect(chooseProfile).toHaveBeenCalledWith(policy.available_profiles, "daytona", "daytona");
    expect(ctx.client.setProfile).toHaveBeenCalledWith("local-deno", policy.revision);
    const sys = ctx.store.getState().messages.find((m) => m.kind === "text" && m.role === "system");
    if (sys?.kind === "text") {
      expect(sys.text).toContain("Profile set to 'local-deno'");
      expect(sys.text).toContain("Restart Fleet to apply");
    }
  });

  it("/profiles skips PATCH when selection is unchanged or cancelled", async () => {
    const { ctx } = makeContext();
    const policy = makePolicy();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi.fn();
    const profiles = listCommands().find((c) => c.name === "profiles");

    if (profiles) {
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile: vi.fn().mockResolvedValue("daytona") } as never,
      });
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile: vi.fn().mockResolvedValue(null) } as never,
      });
    }
    expect(ctx.client.setProfile).not.toHaveBeenCalled();
  });

  it("/profiles can revert a pending selection to the running profile", async () => {
    const { ctx } = makeContext();
    const policy = makePolicy({ active_profile: "daytona", default_profile: "local-deno" });
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi.fn().mockResolvedValue(makePolicy());
    const chooseProfile = vi.fn().mockResolvedValue("daytona");
    const profiles = listCommands().find((c) => c.name === "profiles");

    if (profiles) {
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile } as never,
      });
    }

    expect(chooseProfile).toHaveBeenCalledWith(policy.available_profiles, "daytona", "local-deno");
    expect(ctx.client.setProfile).toHaveBeenCalledWith("daytona", policy.revision);
  });

  it("/profiles treats cancellation and reselecting the pending default as no-ops", async () => {
    const { ctx } = makeContext();
    const policy = makePolicy({ active_profile: "daytona", default_profile: "local-deno" });
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi.fn();
    const profiles = listCommands().find((c) => c.name === "profiles");

    if (profiles) {
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile: vi.fn().mockResolvedValue("local-deno") } as never,
      });
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile: vi.fn().mockResolvedValue(null) } as never,
      });
    }

    expect(ctx.client.setProfile).not.toHaveBeenCalled();
  });
});
