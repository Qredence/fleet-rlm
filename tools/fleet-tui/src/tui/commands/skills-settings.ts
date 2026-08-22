/** Skills and settings slash commands: /skills, /skill, /settings, /profiles. */

import { FleetApiError, type FleetSettingsPolicy } from "../../fleet-api-client.js";
import {
  MAX_PENDING_SKILLS,
  type ConversationStore,
  type PendingSkillSelection,
} from "../store.js";

import type { CommandContext, CommandSpec, SettingsUpdate } from "./registry.js";
import { appendSystem, errorMessage, notifySuccess } from "./shared.js";

export const skillsCommand: CommandSpec = {
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
        if (selections) {
          ctx.store.dispatch({ type: "skill-selection/replace", selections });
          notifySuccess(
            ctx,
            selections.length === 0
              ? "Skill selections cleared."
              : `Skill selections updated for the next Turn (${selections.length}/${MAX_PENDING_SKILLS}).`,
          );
        }
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
};

export const skillCommand: CommandSpec = {
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
};

export const settingsCommand: CommandSpec = {
  name: "settings",
  description:
    "View/edit non-secret config/fleet.toml provider/model policy; never displays or edits .env; restart Fleet to apply",
  usage: "/settings",
  handler: async (_args, ctx) => {
    try {
      const settings = await ctx.client.getSettings();
      if (ctx.presenter) {
        const update = await ctx.presenter.chooseSetting(settings, (next) =>
          saveSettingsUpdate(ctx, next),
        );
        // Compatibility path: presenters without save-callback support (test
        // doubles) resolve the first update; save it once and close as before.
        if (update) {
          await ctx.client.updateSettings(update);
          notifySuccess(ctx, "Saved to config/fleet.toml. Restart Fleet to apply the new policy.");
        }
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
};

export const profilesCommand: CommandSpec = {
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
        notifySuccess(ctx, `Profile set to '${selected}'. Restart Fleet to apply.`);
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
};

/**
 * Interactive /settings save: PATCH one field, flash the result, and on a
 * revision conflict GET a fresh policy so the next edit uses the current
 * revision. Returns the freshest policy, or null after a surfaced failure.
 */
async function saveSettingsUpdate(
  ctx: CommandContext,
  update: SettingsUpdate,
): Promise<FleetSettingsPolicy | null> {
  try {
    const saved = await ctx.client.updateSettings(update);
    notifySuccess(ctx, `Saved ${update.path} to config/fleet.toml. Restart Fleet to apply.`);
    return saved;
  } catch (error) {
    if (error instanceof FleetApiError && error.code === "settings_revision_conflict") {
      try {
        const fresh = await ctx.client.getSettings();
        notifySuccess(
          ctx,
          "Settings changed outside this TUI; the latest policy was reloaded. Re-apply your edit.",
        );
        return fresh;
      } catch (refreshError) {
        appendSystem(ctx.store, `Failed to access settings: ${errorMessage(refreshError)}`);
        return null;
      }
    }
    appendSystem(ctx.store, `Failed to save settings: ${errorMessage(error)}`);
    return null;
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
  if (!existing && pending.length >= MAX_PENDING_SKILLS) {
    appendSystem(store, "At most four unique Skills may be selected for one Turn.");
    return;
  }
  store.dispatch({ type: "skill-selection/pin", selection });
  appendSystem(
    store,
    `${existing ? "Updated" : "Pinned"} ${selection.displayName}@${selection.expectedVersion} for the next Turn.`,
  );
}

function formatSettingValue(value: unknown): string {
  if (value === undefined || value === null) return "(unset)";
  return JSON.stringify(value) ?? "(unset)";
}
