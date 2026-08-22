import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FleetApiClient } from "../../fleet-api-client.js";
import { FleetApiError } from "../../fleet-api-client.js";
import {
  type CommandContext,
  formatVolumeTree,
  listCommands,
  parseInput,
  type SettingsUpdate,
} from "../commands.js";
import { ConversationStore } from "../store.js";

async function makeTempFile(
  name: string,
  content: string,
): Promise<{ path: string; cleanup: () => Promise<void> }> {
  const dir = await mkdtemp(join(tmpdir(), "fleet-command-"));
  const path = join(dir, name);
  await writeFile(path, content, "utf8");
  return { path, cleanup: () => rm(dir, { recursive: true, force: true }) };
}

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
    let resolveUpdate: (session: UpdatedSession) => void = () => {
      throw new Error("updateSession resolve is not armed");
    };
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

  it("/settings formats an unset value without a literal undefined", async () => {
    const { ctx } = makeContext();
    ctx.client.getSettings = vi.fn().mockResolvedValue({
      revision: "a".repeat(64),
      active_profile: "daytona",
      default_profile: "daytona",
      restart_required: true,
      scopes: [
        {
          name: "daytona",
          fields: [
            {
              path: "llm.base_url",
              group: "LLM",
              label: "Base URL",
              value: undefined,
              editor: "text",
              choices: [],
              environment_overridden: false,
            },
          ],
        },
      ],
    });

    const settings = listCommands().find((c) => c.name === "settings");
    if (settings) await settings.handler([], ctx);

    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") {
      expect(last.text).toContain("llm.base_url = (unset)");
      expect(last.text).not.toContain("undefined");
    }
  });

  it("/sessions reports a busy Run instead of silently doing nothing", async () => {
    const { ctx } = makeContext();
    ctx.client.listSessions = vi.fn();
    ctx.store.dispatch({ type: "user/submit", text: "hi" });
    ctx.store.dispatch({ type: "run/start", runId: "run-1", delivery: "live" });

    const sessions = listCommands().find((command) => command.name === "sessions");
    if (sessions) await sessions.handler([], ctx);

    expect(ctx.client.listSessions).not.toHaveBeenCalled();
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") expect(last.text).toContain("Run is in progress");
  });

  it("/cancel forwards to the cancelActiveRun hook while a Run is active", () => {
    const { ctx } = makeContext();
    const cancelSpy = vi.fn();
    ctx.cancelActiveRun = cancelSpy;
    ctx.store.dispatch({ type: "user/submit", text: "hi" });
    ctx.store.dispatch({ type: "run/start", runId: "run-1", delivery: "live" });
    const cancel = listCommands().find((c) => c.name === "cancel");
    if (cancel) cancel.handler([], ctx);
    expect(cancelSpy).toHaveBeenCalledTimes(1);
  });

  it("/cancel is a no-op without an active Run", () => {
    const { ctx } = makeContext();
    const cancelSpy = vi.fn();
    ctx.cancelActiveRun = cancelSpy;
    const cancel = listCommands().find((c) => c.name === "cancel");
    if (cancel) cancel.handler([], ctx);
    expect(cancelSpy).not.toHaveBeenCalled();
    const last = ctx.store.getState().messages[ctx.store.getState().messages.length - 1];
    expect(last?.kind === "text" && last.text).toContain("No active run.");
  });

  it("/cancel during the cancelling phase does not re-cancel or claim there is no Run", async () => {
    const { ctx } = makeContext();
    const cancelSpy = vi.fn();
    ctx.cancelActiveRun = cancelSpy;
    ctx.store.dispatch({ type: "user/submit", text: "hi" });
    ctx.store.dispatch({ type: "run/start", runId: "run-1", delivery: "live" });
    ctx.store.dispatch({ type: "run/cancelling" });

    const cancel = listCommands().find((c) => c.name === "cancel");
    if (cancel) await Promise.resolve(cancel.handler([], ctx));

    expect(cancelSpy).not.toHaveBeenCalled();
    const last = ctx.store.getState().messages.at(-1);
    expect(last?.kind === "text" && last.text).toContain("already in progress");
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
      available_profiles: ["daytona", "daytona-bench"],
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
      expect(sys.text).toContain("daytona-bench");
      expect(sys.text).toContain("current: daytona");
      expect(sys.text).toContain("daytona (current)");
    }
  });

  it("/profiles distinguishes the running and restart-selected profiles", async () => {
    const { ctx } = makeContext();
    ctx.client.getSettings = vi
      .fn()
      .mockResolvedValue(
        makePolicy({ active_profile: "daytona", default_profile: "daytona-bench" }),
      );
    const profiles = listCommands().find((c) => c.name === "profiles");
    if (profiles) await profiles.handler([], ctx);

    const message = ctx.store.getState().messages.at(-1);
    expect(message).toMatchObject({ kind: "text", role: "system" });
    if (message?.kind === "text") {
      expect(message.text).toContain("running: daytona; selected: daytona-bench");
      expect(message.text).toContain("daytona (running)");
      expect(message.text).toContain("daytona-bench (selected)");
    }
  });

  it("/profiles opens the profile picker and PATCHes on selection", async () => {
    const { ctx } = makeContext();
    const policy = makePolicy();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi
      .fn()
      .mockResolvedValue(makePolicy({ default_profile: "daytona-bench" }));
    const chooseProfile = vi.fn().mockResolvedValue("daytona-bench");
    const ctxWithPresenter: CommandContext = { ...ctx, presenter: { chooseProfile } as never };
    const profiles = listCommands().find((c) => c.name === "profiles");
    if (profiles) await profiles.handler([], ctxWithPresenter);
    expect(chooseProfile).toHaveBeenCalledWith(policy.available_profiles, "daytona", "daytona");
    expect(ctx.client.setProfile).toHaveBeenCalledWith("daytona-bench", policy.revision);
    const sys = ctx.store.getState().messages.find((m) => m.kind === "text" && m.role === "system");
    if (sys?.kind === "text") {
      expect(sys.text).toContain("Profile set to 'daytona-bench'");
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
    const policy = makePolicy({ active_profile: "daytona", default_profile: "daytona-bench" });
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

    expect(chooseProfile).toHaveBeenCalledWith(
      policy.available_profiles,
      "daytona",
      "daytona-bench",
    );
    expect(ctx.client.setProfile).toHaveBeenCalledWith("daytona", policy.revision);
  });

  it("/profiles treats cancellation and reselecting the pending default as no-ops", async () => {
    const { ctx } = makeContext();
    const policy = makePolicy({ active_profile: "daytona", default_profile: "daytona-bench" });
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi.fn();
    const profiles = listCommands().find((c) => c.name === "profiles");

    if (profiles) {
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile: vi.fn().mockResolvedValue("daytona-bench") } as never,
      });
      await profiles.handler([], {
        ...ctx,
        presenter: { chooseProfile: vi.fn().mockResolvedValue(null) } as never,
      });
    }

    expect(ctx.client.setProfile).not.toHaveBeenCalled();
  });
});

describe("attachment commands", () => {
  it("/attach uploads a local file and pins it for the next Turn", async () => {
    const { ctx } = makeContext();
    const { path, cleanup } = await makeTempFile("notes.md", "hello fleet");
    try {
      ctx.client.uploadAttachment = vi.fn().mockResolvedValue({
        id: "00000000-0000-4000-8000-0000000000aa",
        filename: "notes.md",
        content_type: "text/plain",
        byte_size: 12,
        checksum_sha256: "abc",
      });
      const attach = listCommands().find((c) => c.name === "attach");
      if (attach) await attach.handler([path], ctx);

      expect(ctx.client.uploadAttachment).toHaveBeenCalledWith(
        expect.objectContaining({ name: "notes.md", contentType: "text/plain" }),
      );
      expect(ctx.store.getState().pendingAttachments).toEqual([
        {
          id: "00000000-0000-4000-8000-0000000000aa",
          filename: "notes.md",
          bytes: 12,
          contentType: "text/plain",
        },
      ]);
      const last = ctx.store.getState().messages.at(-1);
      expect(last).toMatchObject({ kind: "text", role: "system" });
      if (last?.kind === "text") expect(last.text).toContain("Attached notes.md");
    } finally {
      await cleanup();
    }
  });

  it("/attach reports a missing file without pinning", async () => {
    const { ctx } = makeContext();
    ctx.client.uploadAttachment = vi.fn();
    const attach = listCommands().find((c) => c.name === "attach");
    if (attach) await attach.handler(["/no/such/file.txt"], ctx);

    expect(ctx.client.uploadAttachment).not.toHaveBeenCalled();
    expect(ctx.store.getState().pendingAttachments).toEqual([]);
  });

  it("/attach clear and list manage pinned Attachments", async () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "attachment/pin",
      attachment: { id: "a-1", filename: "f.txt", bytes: 3 },
    });
    const attach = listCommands().find((c) => c.name === "attach");
    if (attach) {
      await attach.handler(["list"], ctx);
      const listed = ctx.store.getState().messages.at(-1);
      expect(listed).toMatchObject({ kind: "text", role: "system" });
      if (listed?.kind === "text") expect(listed.text).toContain("f.txt");

      await attach.handler(["clear"], ctx);
    }
    expect(ctx.store.getState().pendingAttachments).toEqual([]);
  });
});

describe("Workspace files/ commands", () => {
  it("/files lists Workspace files/ entries with sizes", async () => {
    const { ctx } = makeContext();
    ctx.client.listWorkspaceFiles = vi.fn().mockResolvedValue({
      entries: [
        {
          path: "report.md",
          kind: "file",
          byte_size: 2048,
          modified_at: null,
          checksum_sha256: null,
        },
        {
          path: "notes",
          kind: "directory",
          byte_size: null,
          modified_at: null,
          checksum_sha256: null,
        },
      ],
      truncated: false,
      next_cursor: null,
    });
    const files = listCommands().find((c) => c.name === "files");
    if (files) await files.handler([], ctx);

    expect(ctx.client.listWorkspaceFiles).toHaveBeenCalledWith({ path: "." });
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") {
      expect(last.text).toContain("Workspace files");
      expect(last.text).toContain("report.md");
      expect(last.text).toContain("notes/");
      expect(last.text).toContain("2.0KB");
    }
  });

  it("/file previews a bounded read", async () => {
    const { ctx } = makeContext();
    ctx.client.readWorkspaceFile = vi.fn().mockResolvedValue({
      path: "report.md",
      content: "line one\nline two",
      next_cursor: null,
      byte_size: 18,
      eof: true,
    });
    const file = listCommands().find((c) => c.name === "file");
    if (file) await file.handler(["report.md"], ctx);

    expect(ctx.client.readWorkspaceFile).toHaveBeenCalledWith("report.md", 8_000);
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") {
      expect(last.text).toContain("Workspace file report.md");
      expect(last.text).toContain("line two");
    }
  });

  it("/file save pages to eof and writes atomically", async () => {
    const { ctx } = makeContext();
    ctx.client.readWorkspaceFile = vi
      .fn()
      .mockResolvedValueOnce({
        path: "big.txt",
        content: "first page",
        next_cursor: "cur-2",
        byte_size: 21,
        eof: false,
      })
      .mockResolvedValueOnce({
        path: "big.txt",
        content: "second page",
        next_cursor: null,
        byte_size: 21,
        eof: true,
      });
    const dir = await mkdtemp(join(tmpdir(), "fleet-file-save-"));
    try {
      const target = join(dir, "out.txt");
      const file = listCommands().find((c) => c.name === "file");
      if (file) await file.handler(["big.txt", "save", target], ctx);

      expect(ctx.client.readWorkspaceFile).toHaveBeenCalledTimes(2);
      expect(ctx.client.readWorkspaceFile).toHaveBeenNthCalledWith(1, "big.txt", 8_000, undefined);
      expect(ctx.client.readWorkspaceFile).toHaveBeenNthCalledWith(2, "big.txt", 8_000, "cur-2");
      expect(await readFile(target, "utf8")).toBe("first pagesecond page");
      const last = ctx.store.getState().messages.at(-1);
      if (last?.kind === "text") expect(last.text).toContain("Saved Workspace file");
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe("artifact commands", () => {
  it("/artifacts lists committed Artifacts from the conversation", () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "message/upsert",
      message: {
        id: "art-1",
        kind: "artifact",
        runId: "run-1",
        artifactId: "00000000-0000-4000-8000-0000000000bb",
        name: "result.bin",
        artifactKind: "file",
        bytes: 42,
        ts: 1,
      },
    });
    const artifacts = listCommands().find((c) => c.name === "artifacts");
    if (artifacts) artifacts.handler([], ctx);

    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") {
      expect(last.text).toContain("00000000-0000-4000-8000-0000000000bb");
      expect(last.text).toContain("result.bin");
    }
  });

  it("/artifact downloads with integrity verification to a local path", async () => {
    const { ctx } = makeContext();
    const payload = new TextEncoder().encode("artifact bytes");
    const digest = createHash("sha256").update(payload).digest("hex");
    ctx.client.downloadArtifact = vi.fn().mockResolvedValue(
      new Response(payload, {
        headers: {
          "content-length": String(payload.length),
          etag: `"${digest}"`,
        },
      }),
    );
    const dir = await mkdtemp(join(tmpdir(), "fleet-artifact-"));
    try {
      const target = join(dir, "out.bin");
      const artifact = listCommands().find((c) => c.name === "artifact");
      if (artifact) await artifact.handler(["art-1", target], ctx);

      expect(await readFile(target)).toEqual(Buffer.from(payload));
      const last = ctx.store.getState().messages.at(-1);
      if (last?.kind === "text") expect(last.text).toContain("Saved verified Artifact");
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe("redo / reload / trace", () => {
  it("/redo resubmits the last prompt through ctx.submit", () => {
    const { ctx } = makeContext();
    const submit = vi.fn();
    ctx.submit = submit;
    ctx.store.dispatch({ type: "user/submit", text: "retry me" });
    ctx.store.dispatch({ type: "run/start", runId: "run-1", delivery: "live" });
    ctx.store.dispatch({
      type: "run/finish",
      finishReason: "stop",
      error: null,
      durationMs: 5,
      checkpointVersion: 1,
    });
    const redo = listCommands().find((c) => c.name === "redo");
    if (redo) redo.handler([], ctx);

    expect(submit).toHaveBeenCalledWith("retry me");
  });

  it("/redo refuses while a Run is in progress", () => {
    const { ctx } = makeContext();
    const submit = vi.fn();
    ctx.submit = submit;
    ctx.store.dispatch({ type: "user/submit", text: "busy" });
    ctx.store.dispatch({ type: "run/start", runId: "run-1", delivery: "live" });
    const redo = listCommands().find((c) => c.name === "redo");
    if (redo) redo.handler([], ctx);

    expect(submit).not.toHaveBeenCalled();
  });

  it("/redo reports when no prompt was submitted in this session", () => {
    const { ctx } = makeContext();
    const redo = listCommands().find((c) => c.name === "redo");
    if (redo) redo.handler([], ctx);

    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") expect(last.text).toContain("No prompt to redo");
  });

  it("/reload re-fetches durable Turns for the current Session", async () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "session-1", title: "T", status: "active", resumed: true },
    });
    ctx.client.getSession = vi.fn().mockResolvedValue({
      id: "session-1",
      title: "T",
      status: "active",
      checkpoint_version: 2,
      created_at: null,
      updated_at: null,
    });
    ctx.client.listTurns = vi.fn().mockResolvedValue([
      {
        id: "turn-1",
        session_id: "session-1",
        role: "assistant",
        sequence: 1,
        status: "completed",
        parts: [{ type: "data-structured-result", data: { value: { answer: "ok" } } }],
        metadata: {},
      },
    ]);
    const reload = listCommands().find((c) => c.name === "reload");
    if (!reload) throw new Error("reload command missing");
    await reload.handler([], ctx);

    expect(ctx.client.listTurns).toHaveBeenCalledWith("session-1");
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") expect(last.text).toContain("Reloaded session session-1.");
  });

  it("/reload keeps pending Attachments, Skills, and the /redo prompt for the same Session", async () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "session-1", title: "T", status: "active", resumed: true },
    });
    ctx.store.dispatch({
      type: "skill-selection/pin",
      selection: { id: "skill-1", expectedVersion: "1.0.0", displayName: "Skill" },
    });
    ctx.store.dispatch({
      type: "attachment/pin",
      attachment: { id: "a-1", filename: "f.txt", bytes: 1 },
    });
    ctx.store.dispatch({ type: "user/prompt-restore", text: "draft prompt" });
    ctx.client.getSession = vi.fn().mockResolvedValue({
      id: "session-1",
      title: "T",
      status: "active",
      checkpoint_version: 2,
      created_at: null,
      updated_at: null,
    });
    ctx.client.listTurns = vi.fn().mockResolvedValue([]);

    const reload = listCommands().find((c) => c.name === "reload");
    if (!reload) throw new Error("reload command missing");
    await reload.handler([], ctx);

    const state = ctx.store.getState();
    expect(state.pendingSkillSelections).toEqual([
      { id: "skill-1", expectedVersion: "1.0.0", displayName: "Skill" },
    ]);
    expect(state.pendingAttachments).toEqual([{ id: "a-1", filename: "f.txt", bytes: 1 }]);
    expect(state.lastPrompt).toBe("draft prompt");
  });

  it("/resume to a different Session never leaks pending Attachments, Skills, or the /redo prompt", async () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "session/init",
      session: { id: "session-1", title: "T", status: "active", resumed: true },
    });
    ctx.store.dispatch({
      type: "skill-selection/pin",
      selection: { id: "skill-1", expectedVersion: "1.0.0", displayName: "Skill" },
    });
    ctx.store.dispatch({
      type: "attachment/pin",
      attachment: { id: "a-1", filename: "f.txt", bytes: 1 },
    });
    ctx.store.dispatch({ type: "user/prompt-restore", text: "draft prompt" });
    ctx.client.getSession = vi.fn().mockResolvedValue({
      id: "session-2",
      title: "Other",
      status: "active",
      checkpoint_version: 1,
      created_at: null,
      updated_at: null,
    });
    ctx.client.listTurns = vi.fn().mockResolvedValue([]);

    const resume = listCommands().find((c) => c.name === "resume");
    if (resume) await resume.handler(["session-2"], ctx);

    const state = ctx.store.getState();
    expect(state.session?.id).toBe("session-2");
    expect(state.pendingSkillSelections).toEqual([]);
    expect(state.pendingAttachments).toEqual([]);
    expect(state.lastPrompt).toBeNull();
  });

  it("/trace shows the full MLflow trace ID", () => {
    const { ctx } = makeContext();
    ctx.store.dispatch({
      type: "run/start",
      runId: "run-1",
      delivery: "live",
      traceId: "trace:abc123",
    });
    const trace = listCommands().find((c) => c.name === "trace");
    if (trace) trace.handler([], ctx);

    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") expect(last.text).toContain("trace:abc123");
  });
});

describe("theme command", () => {
  beforeEach(() => vi.stubEnv("FLEET_TUI_STATE_DIR", tmpdir()));
  afterEach(() => vi.unstubAllEnvs());

  it("/theme lists builtin themes with the current one marked", async () => {
    const { ctx } = makeContext();
    const theme = listCommands().find((c) => c.name === "theme");
    if (theme) await theme.handler([], ctx);
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") {
      expect(last.text).toContain("dark");
      expect(last.text).toContain("light");
    }
  });

  it("/theme sets a builtin theme by name", async () => {
    const { ctx } = makeContext();
    const theme = listCommands().find((c) => c.name === "theme");
    if (theme) await theme.handler(["light"], ctx);
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") expect(last.text).toContain("Theme set to 'light'.");
    if (theme) await theme.handler(["dark"], ctx);
  });

  it("/theme rejects unknown names", async () => {
    const { ctx } = makeContext();
    const theme = listCommands().find((c) => c.name === "theme");
    if (theme) await theme.handler(["nope"], ctx);
    const last = ctx.store.getState().messages.at(-1);
    expect(last).toMatchObject({ kind: "text", role: "system" });
    if (last?.kind === "text") expect(last.text).toContain("Unknown theme");
  });
});

describe("interactive success notifications", () => {
  function policyFixture() {
    return {
      revision: "a".repeat(64),
      active_profile: "daytona",
      default_profile: "daytona",
      available_profiles: ["daytona", "daytona-bench"],
      restart_required: true,
      scopes: [
        {
          name: "daytona",
          fields: [
            {
              path: "rlm.max_iters",
              group: "RLM",
              label: "Max iterations",
              value: 4,
              editor: "number",
              choices: [],
              environment_overridden: false,
            },
          ],
        },
      ],
    } as const;
  }

  function systemTexts(ctx: CommandContext): string[] {
    return ctx.store
      .getState()
      .messages.filter((m) => m.kind === "text" && m.role === "system")
      .map((m) => (m.kind === "text" ? m.text : ""));
  }

  /** Fake presenter that drives one field edit through the save callback. */
  function presenterEditingOnce(update: SettingsUpdate) {
    return {
      chooseSetting: vi.fn(
        async (_settings: unknown, save?: (next: SettingsUpdate) => Promise<unknown>) => {
          if (save) {
            await save(update);
            return null;
          }
          return update;
        },
      ),
    } as never;
  }

  it("/settings saves through the presenter callback and flashes instead of transcript spam", async () => {
    const { ctx } = makeContext();
    const notify = vi.fn();
    const policy = policyFixture();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.updateSettings = vi.fn().mockResolvedValue({ ...policy, revision: "b".repeat(64) });
    const update: SettingsUpdate = {
      revision: policy.revision,
      scope: "daytona",
      path: "rlm.max_iters",
      value: 8,
    };
    const settings = listCommands().find((c) => c.name === "settings");
    if (settings) {
      await settings.handler([], {
        ...ctx,
        notify,
        presenter: presenterEditingOnce(update),
      });
    }

    expect(ctx.client.updateSettings).toHaveBeenCalledWith(update);
    expect(notify).toHaveBeenCalledWith(
      expect.stringContaining("Saved rlm.max_iters to config/fleet.toml"),
    );
    expect(systemTexts(ctx)).toHaveLength(0);
  });

  it("/settings falls back to a system message when no notify callback exists", async () => {
    const { ctx } = makeContext();
    const policy = policyFixture();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.updateSettings = vi.fn().mockResolvedValue(policy);
    const update: SettingsUpdate = {
      revision: policy.revision,
      scope: "daytona",
      path: "rlm.max_iters",
      value: 8,
    };
    const settings = listCommands().find((c) => c.name === "settings");
    if (settings) await settings.handler([], { ...ctx, presenter: presenterEditingOnce(update) });

    expect(systemTexts(ctx).join("\n")).toContain("Saved rlm.max_iters");
  });

  it("/settings reloads the latest policy on a revision conflict and keeps state consistent", async () => {
    const { ctx } = makeContext();
    const notify = vi.fn();
    const policy = policyFixture();
    const fresh = { ...policy, revision: "c".repeat(64) };
    ctx.client.getSettings = vi.fn().mockResolvedValueOnce(policy).mockResolvedValueOnce(fresh);
    ctx.client.updateSettings = vi
      .fn()
      .mockRejectedValue(
        new FleetApiError(409, "Settings changed", "req-1", "settings_revision_conflict"),
      );

    let savedPolicy: unknown;
    const presenter = {
      chooseSetting: vi.fn(
        async (_settings: unknown, save?: (next: SettingsUpdate) => Promise<unknown>) => {
          if (!save) return null;
          savedPolicy = await save({
            revision: policy.revision,
            scope: "daytona",
            path: "rlm.max_iters",
            value: 8,
          });
          return null;
        },
      ),
    } as never;

    const settings = listCommands().find((c) => c.name === "settings");
    if (settings) await settings.handler([], { ...ctx, notify, presenter });

    expect(ctx.client.getSettings).toHaveBeenCalledTimes(2);
    expect(savedPolicy).toBe(fresh);
    expect(notify).toHaveBeenCalledWith(expect.stringContaining("reloaded"));
    expect(systemTexts(ctx).join("\n")).not.toContain("Failed to save settings");
  });

  it("/settings reports a failed save through the transcript error path", async () => {
    const { ctx } = makeContext();
    const policy = policyFixture();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.updateSettings = vi
      .fn()
      .mockRejectedValue(new FleetApiError(422, "Settings value is invalid"));
    const update: SettingsUpdate = {
      revision: policy.revision,
      scope: "daytona",
      path: "rlm.max_iters",
      value: 8,
    };
    const settings = listCommands().find((c) => c.name === "settings");
    if (settings) await settings.handler([], { ...ctx, presenter: presenterEditingOnce(update) });

    expect(systemTexts(ctx).join("\n")).toContain("Failed to save settings");
  });

  it("/settings keeps the compatibility path for presenters that ignore the save callback", async () => {
    const { ctx } = makeContext();
    const policy = policyFixture();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.updateSettings = vi.fn().mockResolvedValue(policy);
    const update: SettingsUpdate = {
      revision: policy.revision,
      scope: "daytona",
      path: "rlm.max_iters",
      value: 8,
    };
    const notify = vi.fn();
    const presenter = { chooseSetting: vi.fn().mockResolvedValue(update) } as never;
    const settings = listCommands().find((c) => c.name === "settings");
    if (settings) await settings.handler([], { ...ctx, presenter, notify });

    expect(ctx.client.updateSettings).toHaveBeenCalledWith(update);
    expect(notify).toHaveBeenCalledWith(expect.stringContaining("Saved"));
    expect(systemTexts(ctx)).toHaveLength(0);
  });

  it("/profiles flashes the selection instead of a transcript message when interactive", async () => {
    const { ctx } = makeContext();
    const notify = vi.fn();
    const policy = policyFixture();
    ctx.client.getSettings = vi.fn().mockResolvedValue(policy);
    ctx.client.setProfile = vi
      .fn()
      .mockResolvedValue({ ...policy, default_profile: "daytona-bench" });
    const presenter = { chooseProfile: vi.fn().mockResolvedValue("daytona-bench") } as never;
    const profiles = listCommands().find((c) => c.name === "profiles");
    if (profiles) await profiles.handler([], { ...ctx, notify, presenter });

    expect(ctx.client.setProfile).toHaveBeenCalledWith("daytona-bench", policy.revision);
    expect(notify).toHaveBeenCalledWith(expect.stringContaining("Profile set to 'daytona-bench'"));
    expect(systemTexts(ctx)).toHaveLength(0);
  });

  it("/theme flashes after applying a picked theme and keeps failures in the transcript", async () => {
    vi.stubEnv("FLEET_TUI_STATE_DIR", tmpdir());
    try {
      const { ctx } = makeContext();
      const notify = vi.fn();
      const theme = listCommands().find((c) => c.name === "theme");
      if (!theme) throw new Error("theme command missing");

      const apply = { chooseTheme: vi.fn().mockResolvedValue("light") } as never;
      await theme.handler([], { ...ctx, notify, presenter: apply });
      expect(notify).toHaveBeenCalledWith(expect.stringContaining("Theme set to 'light'."));
      expect(systemTexts(ctx)).toHaveLength(0);

      const reject = { chooseTheme: vi.fn().mockResolvedValue("not-a-theme") } as never;
      await theme.handler([], { ...ctx, notify, presenter: reject });
      expect(systemTexts(ctx).join("\n")).toContain("Theme not found: not-a-theme");
      if (theme) await theme.handler(["dark"], ctx);
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("/skills flashes the updated selection count when interactive", async () => {
    const { ctx } = makeContext();
    const notify = vi.fn();
    const card = {
      id: "00000000-0000-4000-8000-0000000000ab",
      name: "long-context",
      description: "Long context",
      scope: "system",
      version: "2.0.0",
      trust: "system",
      affordances: [],
      resources_available: true,
    };
    ctx.client.listSkills = vi.fn().mockResolvedValue([card]);
    const presenter = {
      chooseSkills: vi
        .fn()
        .mockResolvedValue([
          { id: card.id, expectedVersion: card.version, displayName: card.name },
        ]),
    } as never;
    const skills = listCommands().find((c) => c.name === "skills");
    if (skills) await skills.handler([], { ...ctx, notify, presenter });

    expect(ctx.store.getState().pendingSkillSelections).toHaveLength(1);
    expect(notify).toHaveBeenCalledWith(expect.stringContaining("1/4"));
    expect(systemTexts(ctx)).toHaveLength(0);
  });
});
