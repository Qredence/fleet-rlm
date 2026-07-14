/** Slash command registry for the Fleet TUI. */

import type { FleetApiClient } from "../fleet-api-client.js";
import { projectDurableTurns } from "./projection.js";
import { newMessageId, type ConversationStore, type Message } from "./store.js";

export type CommandContext = {
  store: ConversationStore;
  client: FleetApiClient;
  cancelActiveRun: () => Promise<void> | void;
  exit: () => void;
};

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
  | { kind: "empty" };

export function parseInput(raw: string): ParsedInput {
  const text = raw.trim();
  if (!text) return { kind: "empty" };
  if (!text.startsWith("/")) return { kind: "message", text };
  const tokens = text.split(/\s+/);
  const name = tokens[0]?.slice(1) ?? "";
  if (!name) return { kind: "message", text };
  const spec = commands.get(name);
  if (!spec) {
    return { kind: "message", text };
  }
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
    const lines = listCommands()
      .map((spec) => `  ${spec.usage.padEnd(28)}  ${spec.description}`)
      .join("\n");
    appendSystem(
      ctx.store,
      `Fleet TUI commands\n\n${lines}\n\nKeybindings:\n  Enter         submit prompt\n  Ctrl+C        cancel current run, second press exits\n  Ctrl+D        exit\n  Arrow Up/Dn   history`,
    );
  },
});

registerCommand({
  name: "clear",
  description: "Clear the visible conversation",
  usage: "/clear",
  handler: (_args, ctx) => {
    ctx.store.dispatch({ type: "clear" });
    if (ctx.store.getState().session) {
      appendSystem(ctx.store, "Conversation cleared. Session is still active.");
    }
  },
});

registerCommand({
  name: "sessions",
  description: "List recent Fleet sessions",
  usage: "/sessions",
  handler: async (_args, ctx) => {
    try {
      const response = await ctx.client.listSessions();
      const items = response.items;
      if (items.length === 0) {
        appendSystem(ctx.store, "No sessions yet.");
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
        `Recent sessions (${response.total} total)\n\n${lines}\n\nUse /resume <id> to switch.`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list sessions: ${errorMessage(error)}`);
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
    try {
      const [session, turns] = await Promise.all([
        ctx.client.getSession(id),
        ctx.client.listTurns(id),
      ]);
      ctx.store.dispatch({
        type: "session/hydrate",
        session: {
          id: session.id,
          title: session.title,
          status: session.status,
          resumed: true,
        },
        events: projectDurableTurns(turns),
      });
      appendSystem(
        ctx.store,
        turns.length > 0 ? `Resumed session ${id}.` : `Resumed session ${id} (no prior turns).`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to resume: ${errorMessage(error)}`);
    }
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
  name: "status",
  description: "Show session, run, and token usage",
  usage: "/status",
  handler: (_args, ctx) => {
    const state = ctx.store.getState();
    const lines = [
      `Session:    ${state.session?.id ?? "(none)"} (${state.session?.status ?? "—"})`,
      `Run:        ${state.run.id ?? "(none)"}  phase=${state.run.phase}  finish=${state.run.finishReason ?? "—"}`,
      `Model:      ${state.run.model ?? "—"}`,
      `Tools:      ${state.run.toolCount}    Steps: ${state.run.completedSteps}`,
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
