/** Slash command registry for the Fleet TUI. */

import { open } from "node:fs/promises";
import { basename } from "node:path";

import { saveArtifact, writeFileAtomic } from "../cli-core.js";
import type {
  FleetApiClient,
  FleetSession,
  FleetSettingsPolicy,
  FleetSkillCard,
} from "../fleet-api-client.js";
import { projectDurableTurns } from "./durable-projection.js";
import { formatBytes, formatTokens, shortId } from "./format.js";
import {
  type ConversationStore,
  type Message,
  newMessageId,
  type PendingSkillSelection,
} from "./store.js";
import { getAvailableThemes, getThemeName, setTheme } from "./theme.js";
import { committedTokenCounts } from "./usage-summary.js";

export type CommandContext = {
  store: ConversationStore;
  client: FleetApiClient;
  cancelActiveRun: () => Promise<void> | void;
  exit: () => void;
  /** Submit a prompt as if typed in the editor (used by /redo). */
  submit?: (text: string) => void;
  presenter?: CommandPresenter;
};

export type SettingsUpdate = {
  revision: string;
  scope: string;
  path: string;
  value: string | number | boolean | string[] | null;
};

export interface CommandPresenter {
  showHelp(commands: CommandSpec[]): void;
  chooseSession(sessions: FleetSession[]): Promise<string | null>;
  chooseSkills(
    skills: FleetSkillCard[],
    current: PendingSkillSelection[],
  ): Promise<PendingSkillSelection[] | null>;
  chooseSetting(settings: FleetSettingsPolicy): Promise<SettingsUpdate | null>;
  chooseTheme(themes: string[], current: string | undefined): Promise<string | null>;
  chooseProfile(
    profiles: string[],
    active: string | undefined,
    selected: string | undefined,
  ): Promise<string | null>;
}

export type CommandHandler = (args: string[], ctx: CommandContext) => Promise<void> | void;

export type CommandSpec = {
  name: string;
  description: string;
  usage: string;
  handler: CommandHandler;
};

const commands = new Map<string, CommandSpec>();

export function registerCommand(spec: CommandSpec): void {
  commands.set(spec.name, spec);
}

export function getCommand(name: string): CommandSpec | undefined {
  return commands.get(name);
}

export function listCommands(): CommandSpec[] {
  return Array.from(commands.values());
}

export type ParsedInput =
  | { kind: "command"; spec: CommandSpec; args: string[] }
  | { kind: "message"; text: string }
  | { kind: "unknown-command"; name: string }
  | { kind: "empty" };

export function parseInput(raw: string): ParsedInput {
  const text = raw.trim();
  if (!text) return { kind: "empty" };
  if (!text.startsWith("/")) return { kind: "message", text };
  const tokens = text.split(/\s+/);
  const name = tokens[0]?.slice(1) ?? "";
  if (!name) return { kind: "message", text };
  const spec = commands.get(name);
  if (!spec) return { kind: "unknown-command", name };
  return { kind: "command", spec, args: tokens.slice(1) };
}

function appendMessage(store: ConversationStore, message: Message): void {
  store.dispatch({ type: "message/upsert", message });
}

function appendSystem(store: ConversationStore, text: string): void {
  appendMessage(store, {
    id: newMessageId("system"),
    kind: "text",
    role: "system",
    text,
    ts: Date.now(),
    streaming: false,
  });
}

registerCommand({
  name: "help",
  description: "List all slash commands",
  usage: "/help",
  handler: (_args, ctx) => {
    if (ctx.presenter) {
      ctx.presenter.showHelp(listCommands());
      return;
    }
    const lines = listCommands()
      .map((spec) => `  ${spec.usage.padEnd(28)}  ${spec.description}`)
      .join("\n");
    appendSystem(
      ctx.store,
      `Fleet TUI commands\n\n${lines}\n\nKeybindings:\n  Enter         submit prompt\n  Shift+Enter   insert newline\n  Escape        cancel current Run\n  Ctrl+C        clear editor; press twice while empty to exit\n  Ctrl+D        delete forward, or exit when the editor is empty\n  Arrow Up/Dn   history`,
    );
  },
});

registerCommand({
  name: "clear",
  description: "Reset the local view without changing durable Session history",
  usage: "/clear",
  handler: (_args, ctx) => {
    ctx.store.dispatch({ type: "clear" });
    if (ctx.store.getState().session) {
      appendSystem(
        ctx.store,
        "Local view reset. Durable Session history is unchanged and returns on resume.",
      );
    }
  },
});

registerCommand({
  name: "sessions",
  description: "Find and switch to an active Fleet Session",
  usage: "/sessions [title search]",
  handler: async (args, ctx) => {
    try {
      const search = args.join(" ").trim();
      const response = await ctx.client.listSessions({
        limit: 100,
        status: "active",
        ...(search ? { search } : {}),
      });
      const items = response.items;
      if (items.length === 0) {
        appendSystem(
          ctx.store,
          search ? `No active Sessions match “${search}”.` : "No active Sessions yet.",
        );
        return;
      }
      if (ctx.presenter) {
        const id = await ctx.presenter.chooseSession(items);
        if (id) await resumeSession(id, ctx);
        return;
      }
      const lines = items
        .map(
          (item, index) =>
            `  ${String(index + 1).padStart(2)}. ${item.id}  ${item.title}  (${item.status})`,
        )
        .join("\n");
      appendSystem(
        ctx.store,
        `Active Sessions (${response.total} total)\n\n${lines}\n\nUse /resume <id> to switch.`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list sessions: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "rename",
  description: "Rename the current Fleet Session",
  usage: "/rename <title>",
  handler: async (args, ctx) => {
    const session = ctx.store.getState().session;
    const title = args.join(" ").trim();
    if (!session) {
      appendSystem(ctx.store, "No current Session to rename.");
      return;
    }
    if (!title) {
      appendSystem(ctx.store, "Usage: /rename <title>");
      return;
    }
    try {
      const updated = await ctx.client.updateSession(session.id, { title });
      if (ctx.store.getState().session?.id !== session.id) return;
      ctx.store.dispatch({
        type: "session/init",
        session: {
          id: updated.id,
          title: updated.title,
          status: updated.status,
          resumed: session.resumed,
        },
      });
      appendSystem(ctx.store, `Session renamed to “${updated.title}”.`);
    } catch (error) {
      appendSystem(ctx.store, `Failed to rename Session: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "resume",
  description: "Resume a different Fleet session",
  usage: "/resume <session-uuid>",
  handler: async (args, ctx) => {
    const id = args[0];
    if (!id) {
      appendSystem(ctx.store, "Usage: /resume <session-uuid>");
      return;
    }
    await resumeSession(id, ctx);
  },
});

registerCommand({
  name: "cancel",
  description: "Cancel the current run",
  usage: "/cancel",
  handler: async (_args, ctx) => {
    const phase = ctx.store.getState().run.phase;
    if (phase !== "submitting" && phase !== "running") {
      appendSystem(ctx.store, "No active run.");
      return;
    }
    await ctx.cancelActiveRun();
    appendSystem(ctx.store, "Cancellation requested.");
  },
});

registerCommand({
  name: "skills",
  description: "List Skills available for the next Turn",
  usage: "/skills",
  handler: async (_args, ctx) => {
    try {
      const cards = await ctx.client.listSkills();
      if (cards.length === 0) {
        appendSystem(ctx.store, "No discoverable Skills are available.");
        return;
      }
      if (ctx.presenter) {
        const selections = await ctx.presenter.chooseSkills(
          cards,
          ctx.store.getState().pendingSkillSelections,
        );
        if (selections) ctx.store.dispatch({ type: "skill-selection/replace", selections });
        return;
      }
      const lines = cards
        .map((card) => `  ${card.name}@${card.version}  ${card.id}\n    ${card.description}`)
        .join("\n");
      appendSystem(
        ctx.store,
        `Discoverable Skills\n\n${lines}\n\nUse /skill <name-or-id> to pin the current version.`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list Skills: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "skill",
  description: "Pin or clear Skills for the next Turn",
  usage: "/skill <name-or-id>|<hidden-uuid>@<version>|clear",
  handler: async (args, ctx) => {
    const reference = args[0];
    if (!reference || args.length !== 1) {
      appendSystem(
        ctx.store,
        "Usage: /skill <name-or-id> | /skill <hidden-uuid>@<version> | /skill clear",
      );
      return;
    }
    if (reference === "clear") {
      ctx.store.dispatch({ type: "skill-selection/clear" });
      appendSystem(ctx.store, "Pending Skill selections cleared.");
      return;
    }

    const exact = parseExactHiddenSelection(reference);
    if (exact) {
      pinSkill(ctx.store, exact);
      return;
    }

    try {
      const cards = await ctx.client.listSkills();
      const card = cards.find(
        (candidate) => candidate.name === reference || candidate.id === reference,
      );
      if (!card) {
        appendSystem(
          ctx.store,
          `Skill ${reference} is not discoverable. Hidden Skills require /skill <uuid>@<version>.`,
        );
        return;
      }
      pinSkill(ctx.store, {
        id: card.id,
        expectedVersion: card.version,
        displayName: card.name,
      });
    } catch (error) {
      appendSystem(ctx.store, `Failed to resolve Skill: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "settings",
  description:
    "View/edit non-secret config/fleet.toml provider/model policy; never displays or edits .env; restart Fleet to apply",
  usage: "/settings",
  handler: async (_args, ctx) => {
    try {
      const settings = await ctx.client.getSettings();
      if (ctx.presenter) {
        const update = await ctx.presenter.chooseSetting(settings);
        if (!update) return;
        await ctx.client.updateSettings(update);
        appendSystem(
          ctx.store,
          "Saved to config/fleet.toml. Restart Fleet to apply the new policy.",
        );
        return;
      }
      const lines = settings.scopes.flatMap((scope) => [
        `[${scope.name}]`,
        ...scope.fields.map((field) => `  ${field.path} = ${formatSettingValue(field.value)}`),
      ]);
      appendSystem(
        ctx.store,
        `Fleet settings (restart required after save)\n\n${lines.join("\n")}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to access settings: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "profiles",
  description: "Switch the active Fleet profile (restart required)",
  usage: "/profiles",
  handler: async (_args, ctx) => {
    try {
      const settings = await ctx.client.getSettings();
      const profiles =
        settings.available_profiles ??
        settings.scopes.map((scope) => scope.name).filter((name) => name !== "defaults");
      const active = settings.active_profile ?? undefined;
      const selectedForRestart = settings.default_profile ?? active;
      if (ctx.presenter) {
        const selected = await ctx.presenter.chooseProfile(profiles, active, selectedForRestart);
        if (!selected || selected === selectedForRestart) return;
        await ctx.client.setProfile(selected, settings.revision);
        appendSystem(ctx.store, `Profile set to '${selected}'. Restart Fleet to apply.`);
        return;
      }
      const lines = profiles.map((name) => {
        let suffix = "";
        if (name === active && name === selectedForRestart) suffix = " (current)";
        else if (name === active) suffix = " (running)";
        else if (name === selectedForRestart) suffix = " (selected)";
        return `  ${name}${suffix}`;
      });
      let state = "";
      if (active && selectedForRestart && active !== selectedForRestart) {
        state = ` (running: ${active}; selected: ${selectedForRestart})`;
      } else if (active) {
        state = ` (current: ${active})`;
      } else if (selectedForRestart) {
        state = ` (selected: ${selectedForRestart})`;
      }
      appendSystem(
        ctx.store,
        `Fleet profiles${state} (restart to apply)\n\n${lines.join("\n")}\n\nSwitch with /profiles in the interactive TUI, or /settings to edit policy values.`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to access profiles: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "volume",
  description: "Show the Workspace Volume file tree",
  usage: "/volume [root]",
  handler: async (args, ctx) => {
    if (args.length > 1) {
      appendSystem(ctx.store, "Usage: /volume [root]");
      return;
    }
    const root = args[0] ?? ".";
    try {
      const tree = await ctx.client.listVolumeTree({ root });
      const rendered = formatVolumeTree([...(tree.directories ?? []), ...tree.paths]);
      appendSystem(
        ctx.store,
        `Workspace Volume${root === "." ? "" : ` (${root})`}\n\n${rendered}${tree.truncated ? "\n\n…tree truncated; narrow the root or use a deeper command." : ""}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list Workspace Volume: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "status",
  description: "Show session, run, and token usage",
  usage: "/status",
  handler: (_args, ctx) => {
    const state = ctx.store.getState();
    const usage = committedTokenCounts(state.messages);
    const lines = [
      `Session:    ${state.session?.title ?? "(none)"}  ${state.session?.id ?? "—"} (${state.session?.status ?? "—"})`,
      `Run:        ${state.run.id ?? "(none)"}  phase=${state.run.phase}  finish=${state.run.finishReason ?? "—"}`,
      `Delivery:   ${state.run.delivery ?? "—"}  outcome=${state.run.outcome ?? "—"}`,
      `Trace:      ${state.run.traceId ?? "—"}`,
      `Usage:      observed committed input=${formatObservedTokens(usage.input)} output=${formatObservedTokens(usage.output)}`,
      `Tools:      ${state.run.toolCount}    Steps: ${state.run.completedSteps}/${state.run.startedSteps}`,
      `Skills:     ${formatPendingSkills(state.pendingSkillSelections)}`,
      `Messages:   ${state.messages.length}`,
    ];
    appendSystem(ctx.store, lines.join("\n"));
  },
});

registerCommand({
  name: "attach",
  description: "Upload local files as Attachments pinned to the next Turn",
  usage: "/attach <path>… | clear | list",
  handler: async (args, ctx) => {
    if (args.length === 0 || args[0] === "list") {
      const pending = ctx.store.getState().pendingAttachments;
      if (pending.length === 0) {
        appendSystem(ctx.store, "No Attachments pinned for the next Turn.");
        return;
      }
      const lines = pending
        .map(
          (attachment, index) =>
            `  ${String(index + 1).padStart(2)}. ${attachment.filename}  ${formatBytes(attachment.bytes)}  ${shortId(attachment.id)}`,
        )
        .join("\n");
      appendSystem(ctx.store, `Pinned Attachments for the next Turn\n\n${lines}`);
      return;
    }
    if (args[0] === "clear") {
      ctx.store.dispatch({ type: "attachment/clear" });
      appendSystem(ctx.store, "Pending Attachments cleared.");
      return;
    }
    for (const arg of args) {
      try {
        const handle = await open(arg, "r");
        let bytes: Buffer;
        try {
          const info = await handle.stat();
          if (!info.isFile()) {
            appendSystem(ctx.store, `${arg} is not a regular file.`);
            continue;
          }
          bytes = await handle.readFile();
        } finally {
          await handle.close();
        }
        const ref = await ctx.client.uploadAttachment({
          name: basename(arg),
          bytes,
          contentType: contentTypeFor(arg),
        });
        ctx.store.dispatch({
          type: "attachment/pin",
          attachment: {
            id: ref.id,
            filename: ref.filename,
            bytes: ref.byte_size,
            contentType: ref.content_type ?? undefined,
          },
        });
        appendSystem(
          ctx.store,
          `Attached ${ref.filename} (${formatBytes(ref.byte_size)}) for the next Turn.`,
        );
      } catch (error) {
        appendSystem(ctx.store, `Failed to attach ${arg}: ${errorMessage(error)}`);
      }
    }
  },
});

registerCommand({
  name: "files",
  description: "List the Workspace files/ root via /api/files",
  usage: "/files [path]",
  handler: async (args, ctx) => {
    if (args.length > 1) {
      appendSystem(ctx.store, "Usage: /files [path]");
      return;
    }
    try {
      const listing = await ctx.client.listWorkspaceFiles({ path: args[0] ?? "." });
      if (listing.entries.length === 0) {
        appendSystem(ctx.store, `No Workspace files/ entries under “${args[0] ?? "."}”.`);
        return;
      }
      const lines = listing.entries
        .map((entry) =>
          entry.kind === "directory"
            ? `  ${entry.path}/`
            : `  ${entry.path}  ${entry.byte_size == null ? "—" : formatBytes(entry.byte_size)}`,
        )
        .join("\n");
      appendSystem(
        ctx.store,
        `Workspace files${args[0] ? ` (${args[0]})` : ""}\n\n${lines}${listing.truncated ? "\n\n…listing truncated; use /files <path> to narrow." : ""}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list Workspace files: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "file",
  description: "Show or save one file from the Workspace files/ root",
  usage: "/file <path> [save <localPath>]",
  handler: async (args, ctx) => {
    const path = args[0];
    if (!path) {
      appendSystem(ctx.store, "Usage: /file <path> [save <localPath>]");
      return;
    }
    if (args[1] === "save") {
      const localPath = args[2];
      if (!localPath) {
        appendSystem(ctx.store, "Usage: /file <path> save <localPath>");
        return;
      }
      try {
        let content = "";
        let cursor: string | undefined;
        let pages = 0;
        do {
          const page = await ctx.client.readWorkspaceFile(path, cursor ? undefined : 8_000);
          content += page.content;
          cursor = page.next_cursor ?? undefined;
          pages += 1;
          if (pages > 1_000) throw new Error("Workspace file is too large to save");
        } while (cursor);
        await writeFileAtomic(localPath, Buffer.from(content, "utf8"));
        appendSystem(ctx.store, `Saved Workspace file to ${localPath}.`);
      } catch (error) {
        appendSystem(ctx.store, `Failed to save ${path}: ${errorMessage(error)}`);
      }
      return;
    }
    if (args.length > 1) {
      appendSystem(ctx.store, "Usage: /file <path> [save <localPath>]");
      return;
    }
    try {
      const page = await ctx.client.readWorkspaceFile(path, 8_000);
      const preview = page.content.slice(0, 8_000);
      const lines = preview
        .split("\n")
        .map((line) => `  ${line}`)
        .join("\n");
      appendSystem(
        ctx.store,
        `Workspace file ${path} (${formatBytes(page.byte_size)})\n\n${lines}${page.content.length >= 8_000 ? "\n\n…preview truncated; use /file <path> save <localPath> for the full file." : ""}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to read ${path}: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "artifact",
  description: "Download and verify an Artifact to a local path",
  usage: "/artifact <artifactId> <localPath>",
  handler: async (args, ctx) => {
    const [artifactId, localPath] = args;
    if (!artifactId || !localPath || args.length !== 2) {
      appendSystem(ctx.store, "Usage: /artifact <artifactId> <localPath>");
      return;
    }
    try {
      await saveArtifact(ctx.client, artifactId, localPath);
      appendSystem(ctx.store, `Saved verified Artifact to ${localPath}.`);
    } catch (error) {
      appendSystem(ctx.store, `Failed to save Artifact: ${errorMessage(error)}`);
    }
  },
});

registerCommand({
  name: "artifacts",
  description: "List Artifacts committed in this conversation",
  usage: "/artifacts",
  handler: (_args, ctx) => {
    const artifacts = ctx.store
      .getState()
      .messages.filter(
        (message): message is Extract<Message, { kind: "artifact" }> => message.kind === "artifact",
      );
    if (artifacts.length === 0) {
      appendSystem(ctx.store, "No Artifacts in this conversation.");
      return;
    }
    const lines = artifacts
      .map(
        (artifact, index) =>
          `  ${String(index + 1).padStart(2)}. ${artifact.artifactId}  ${artifact.name}  ${formatBytes(artifact.bytes)}  (${artifact.artifactKind})`,
      )
      .join("\n");
    appendSystem(
      ctx.store,
      `Artifacts\n\n${lines}\n\nUse /artifact <id> <localPath> to download and verify.`,
    );
  },
});

registerCommand({
  name: "redo",
  description: "Resubmit the last prompt with a fresh idempotency key",
  usage: "/redo",
  handler: (_args, ctx) => {
    const state = ctx.store.getState();
    if (["submitting", "running", "cancelling"].includes(state.run.phase)) {
      appendSystem(ctx.store, "A Run is in progress; cancel it before redoing.");
      return;
    }
    const prompt = state.lastPrompt;
    if (!prompt) {
      appendSystem(ctx.store, "No prompt to redo in this TUI session.");
      return;
    }
    ctx.submit?.(prompt);
  },
});

registerCommand({
  name: "reload",
  description: "Re-fetch committed Turns for the current Session",
  usage: "/reload",
  handler: async (_args, ctx) => {
    const session = ctx.store.getState().session;
    if (!session) {
      appendSystem(ctx.store, "No active Session to reload.");
      return;
    }
    await loadSession(ctx, session.id, "Reloaded");
  },
});

registerCommand({
  name: "trace",
  description: "Show the full MLflow trace ID for the current Run",
  usage: "/trace",
  handler: (_args, ctx) => {
    const traceId = ctx.store.getState().run.traceId;
    appendSystem(
      ctx.store,
      traceId ? `Trace: ${traceId}` : "No trace ID recorded for the current Run.",
    );
  },
});

registerCommand({
  name: "theme",
  description: "List or switch the TUI color theme",
  usage: "/theme [name]",
  handler: async (args, ctx) => {
    const themes = await getAvailableThemes();
    const current = getThemeName();
    if (args.length === 0) {
      if (ctx.presenter) {
        const selected = await ctx.presenter.chooseTheme(themes, current);
        if (!selected || selected === current) return;
        const result = await setTheme(selected);
        appendSystem(
          ctx.store,
          result.success ? `Theme set to '${selected}'.` : (result.error ?? "Failed to set theme."),
        );
        return;
      }
      const lines = themes
        .map((name) => `  ${name}${name === current ? " (current)" : ""}`)
        .join("\n");
      appendSystem(ctx.store, `Themes\n\n${lines}\n\nUse /theme <name> to switch.`);
      return;
    }
    if (args.length !== 1) {
      appendSystem(ctx.store, "Usage: /theme [name]");
      return;
    }
    const name = args[0] ?? "";
    if (!themes.includes(name)) {
      appendSystem(ctx.store, `Unknown theme: ${name}`);
      return;
    }
    const result = await setTheme(name);
    appendSystem(
      ctx.store,
      result.success ? `Theme set to '${name}'.` : (result.error ?? "Failed to set theme."),
    );
  },
});

registerCommand({
  name: "exit",
  description: "Exit the TUI",
  usage: "/exit",
  handler: (_args, ctx) => {
    ctx.exit();
  },
});

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

async function resumeSession(id: string, ctx: CommandContext): Promise<void> {
  await loadSession(ctx, id, "Resumed");
}

async function loadSession(ctx: CommandContext, id: string, verb: string): Promise<void> {
  try {
    const pendingSkillSelections = ctx.store.getState().pendingSkillSelections;
    const [session, turns] = await Promise.all([
      ctx.client.getSession(id),
      ctx.client.listTurns(id),
    ]);
    ctx.store.dispatch({
      type: "session/hydrate",
      session: { id: session.id, title: session.title, status: session.status, resumed: true },
      events: projectDurableTurns(turns),
    });
    if (pendingSkillSelections.length > 0) {
      ctx.store.dispatch({ type: "skill-selection/replace", selections: pendingSkillSelections });
    }
    appendSystem(
      ctx.store,
      turns.length ? `${verb} session ${id}.` : `${verb} session ${id} (no prior turns).`,
    );
  } catch (error) {
    appendSystem(ctx.store, `Failed to ${verb.toLowerCase()} session: ${errorMessage(error)}`);
  }
}

function parseExactHiddenSelection(reference: string): PendingSkillSelection | null {
  const match = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})@([^\s@]+)$/i.exec(
    reference,
  );
  if (!match?.[1] || !match[2]) return null;
  return {
    id: match[1].toLowerCase(),
    expectedVersion: match[2],
    displayName: `${match[1].slice(0, 8)}…`,
  };
}

function pinSkill(store: ConversationStore, selection: PendingSkillSelection): void {
  const pending = store.getState().pendingSkillSelections;
  const existing = pending.find((candidate) => candidate.id === selection.id);
  if (!existing && pending.length >= 4) {
    appendSystem(store, "At most four unique Skills may be selected for one Turn.");
    return;
  }
  store.dispatch({ type: "skill-selection/pin", selection });
  appendSystem(
    store,
    `${existing ? "Updated" : "Pinned"} ${selection.displayName}@${selection.expectedVersion} for the next Turn.`,
  );
}

function formatPendingSkills(selections: readonly PendingSkillSelection[]): string {
  if (selections.length === 0) return "(none)";
  return selections
    .map((selection) => `${selection.displayName}@${selection.expectedVersion}`)
    .join(", ");
}

function formatSettingValue(value: unknown): string {
  return JSON.stringify(value);
}

export function formatVolumeTree(paths: readonly string[]): string {
  if (paths.length === 0) return "(empty)";
  const root = new Map<string, Map<string, unknown>>();
  for (const raw of paths) {
    const parts = raw.replace(/^\.\//, "").split("/").filter(Boolean);
    let node = root;
    for (const part of parts) {
      let child = node.get(part);
      if (!child) {
        child = new Map<string, unknown>();
        node.set(part, child);
      }
      node = child as Map<string, Map<string, unknown>>;
    }
  }
  const lines: string[] = [];
  const visit = (node: Map<string, unknown>, prefix: string): void => {
    const entries = [...node.entries()].sort(([a], [b]) => a.localeCompare(b));
    entries.forEach(([name, child], index) => {
      const last = index === entries.length - 1;
      lines.push(`${prefix}${last ? "└── " : "├── "}${name}`);
      if ((child as Map<string, unknown>).size > 0)
        visit(child as Map<string, unknown>, `${prefix}${last ? "    " : "│   "}`);
    });
  };
  visit(root, "");
  return lines.join("\n");
}

function formatObservedTokens(value: number | null): string {
  return value === null ? "—" : formatTokens(value);
}

const TEXT_EXTENSIONS = new Set([
  "csv",
  "html",
  "js",
  "json",
  "log",
  "md",
  "py",
  "sh",
  "toml",
  "ts",
  "txt",
  "xml",
  "yaml",
  "yml",
]);

function contentTypeFor(path: string): string {
  const extension = basename(path).split(".").pop()?.toLowerCase();
  return extension && TEXT_EXTENSIONS.has(extension) ? "text/plain" : "application/octet-stream";
}
