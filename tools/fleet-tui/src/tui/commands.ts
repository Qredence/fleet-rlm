/** Slash command registry for the Fleet TUI. */

import type {
  FleetApiClient,
  FleetSession,
  FleetSettingsPolicy,
  FleetSkillCard,
} from "../fleet-api-client.js";
import { projectDurableTurns } from "./projection.js";
import {
  newMessageId,
  type ConversationStore,
  type Message,
  type PendingSkillSelection,
} from "./store.js";
import { formatTokens } from "./format.js";
import { committedTokenCounts } from "./usage-summary.js";

export type CommandContext = {
  store: ConversationStore;
  client: FleetApiClient;
  cancelActiveRun: () => Promise<void> | void;
  exit: () => void;
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
  description: "View and edit local Fleet policy settings",
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
      const rendered = formatVolumeTree([...tree.directories, ...tree.paths]);
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
      turns.length ? `Resumed session ${id}.` : `Resumed session ${id} (no prior turns).`,
    );
  } catch (error) {
    appendSystem(ctx.store, `Failed to resume: ${errorMessage(error)}`);
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
