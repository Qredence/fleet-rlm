/** Session lifecycle slash commands: /sessions, /rename, /resume, /reload. */

import { projectDurableTurns } from "../durable-projection.js";

import type { CommandContext, CommandSpec } from "./registry.js";
import { appendSystem, errorMessage } from "./shared.js";

export const sessionsCommand: CommandSpec = {
  name: "sessions",
  description: "Find and switch to an active Fleet Session",
  usage: "/sessions [title search]",
  handler: async (args, ctx) => {
    const phase = ctx.store.getState().run.phase;
    if (phase === "submitting" || phase === "running" || phase === "cancelling") {
      appendSystem(
        ctx.store,
        "A Run is in progress; /sessions is unavailable until it settles. Use /cancel to stop the Run first.",
      );
      return;
    }
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
};

export const renameCommand: CommandSpec = {
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
};

export const resumeCommand: CommandSpec = {
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
};

export const reloadCommand: CommandSpec = {
  name: "reload",
  description: "Re-fetch committed Turns for the current Session",
  usage: "/reload",
  handler: async (_args, ctx) => {
    const session = ctx.store.getState().session;
    if (!session) {
      appendSystem(ctx.store, "No active Session to reload.");
      return;
    }
    await loadSession(ctx, session.id, "Reloaded", "reload");
  },
};

/**
 * Resumes a Fleet Session by loading it into the current session state.
 *
 * @param id - The UUID of the session to resume
 * @param ctx - The command execution context
 */
async function resumeSession(id: string, ctx: CommandContext): Promise<void> {
  await loadSession(ctx, id, "Resumed", "resume");
}

/**
 * Loads a session and restores its committed turns.
 *
 * @param id - The session identifier to load
 * @param verb - The past-tense action used in the success message
 * @param actionVerb - The action used in the failure message
 */
async function loadSession(
  ctx: CommandContext,
  id: string,
  verb: string,
  actionVerb: string,
): Promise<void> {
  try {
    const [session, turns] = await Promise.all([
      ctx.client.getSession(id),
      ctx.client.listTurns(id),
    ]);
    // "session/hydrate" owns pending-state continuity: it keeps pinned Skills,
    // Attachments, and the /redo prompt for the SAME Session and clears them
    // when switching Sessions, atomically with the message projection.
    ctx.store.dispatch({
      type: "session/hydrate",
      session: { id: session.id, title: session.title, status: session.status, resumed: true },
      events: projectDurableTurns(turns),
    });
    appendSystem(
      ctx.store,
      turns.length ? `${verb} session ${id}.` : `${verb} session ${id} (no prior turns).`,
    );
  } catch (error) {
    appendSystem(ctx.store, `Failed to ${actionVerb} session: ${errorMessage(error)}`);
  }
}
