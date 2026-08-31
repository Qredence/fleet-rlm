/** Status, theme, and general slash commands: /help, /clear, /cancel, /status, /redo, /trace, /theme, /exit. */

import { formatObservedTokens } from "../format.js";
import type { PendingSkillSelection } from "../store.js";
import { getAvailableThemes, getThemeName, setTheme } from "../theme.js";
import { committedTokenCounts } from "../usage-summary.js";

import { listCommands, type CommandSpec } from "./registry.js";
import { appendSystem, notifySuccess } from "./shared.js";

export const helpCommand: CommandSpec = {
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
      `Fleet TUI commands\n\n${lines}\n\nKeybindings:\n  Enter         submit prompt\n  Shift+Enter   insert newline\n  Escape        cancel current Run\n  Ctrl+O        fold the latest Tool, code, or output card\n  Ctrl+Shift+F  search the transcript\n  PgUp/PgDn     scroll the transcript\n  Home/End      jump to transcript start/end\n  Ctrl+C        clear editor; press twice while empty to exit\n  Ctrl+D        delete forward, or exit when the editor is empty\n  Ctrl+Z        suspend Fleet\n  Arrow Up/Dn   editor history`,
    );
  },
};

export const clearCommand: CommandSpec = {
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
};

export const cancelCommand: CommandSpec = {
  name: "cancel",
  description: "Cancel the current run",
  usage: "/cancel",
  handler: async (_args, ctx) => {
    const phase = ctx.store.getState().run.phase;
    if (phase === "cancelling") {
      appendSystem(ctx.store, "Run cancellation is already in progress.");
      return;
    }
    if (phase !== "submitting" && phase !== "running") {
      appendSystem(ctx.store, "No active run.");
      return;
    }
    await ctx.cancelActiveRun();
    appendSystem(ctx.store, "Cancellation requested.");
  },
};

export const statusCommand: CommandSpec = {
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
};

export const redoCommand: CommandSpec = {
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
};

export const traceCommand: CommandSpec = {
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
};

export const themeCommand: CommandSpec = {
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
        if (result.success) {
          notifySuccess(ctx, `Theme set to '${selected}'.`);
        } else {
          appendSystem(ctx.store, result.error ?? "Failed to set theme.");
        }
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
    if (result.success) {
      notifySuccess(ctx, `Theme set to '${name}'.`);
    } else {
      appendSystem(ctx.store, result.error ?? "Failed to set theme.");
    }
  },
};

export const exitCommand: CommandSpec = {
  name: "exit",
  description: "Exit the TUI",
  usage: "/exit",
  handler: (_args, ctx) => {
    ctx.exit();
  },
};

/**
 * Formats pending skill selections for display.
 *
 * @param selections - The pending skill selections to format
 * @returns A comma-separated list of skill names and expected versions, or `(none)` when no selections are provided
 */
function formatPendingSkills(selections: readonly PendingSkillSelection[]): string {
  if (selections.length === 0) return "(none)";
  return selections
    .map((selection) => `${selection.displayName}@${selection.expectedVersion}`)
    .join(", ");
}
