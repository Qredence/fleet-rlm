/** Skills and settings slash commands: /skills, /skill, /settings. */

import { FleetApiError, type FleetSettingsPolicy } from "../../fleet-api-client.js";
import {
  MAX_PENDING_SKILLS,
  type ConversationStore,
  type PendingSkillSelection,
} from "../store.js";

import type {
  CommandContext,
  CommandSpec,
  SettingsBatchUpdate,
  SettingsUpdate,
} from "./registry.js";
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
          await ctx.client.applySettings(update.revision, [
            { path: update.path, value: update.value },
          ]);
          notifySuccess(ctx, "Saved to config/fleet.toml. Restart Fleet to apply the new policy.");
        }
        return;
      }
      const lines = settings.fields.map(
        (field) => `  ${field.path} = ${formatSettingValue(field.value)}`,
      );
      appendSystem(
        ctx.store,
        `Fleet settings (restart required after save)\n\n${lines.join("\n")}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to access settings: ${errorMessage(error)}`);
    }
  },
};

/**
 * Saves a Fleet settings change and refreshes the policy when the revision is outdated.
 *
 * @param update - The settings field and value to save
 * @returns The saved or refreshed settings policy, or `null` if the operation fails
 */
async function saveSettingsUpdate(
  ctx: CommandContext,
  update: SettingsUpdate | SettingsBatchUpdate,
): Promise<FleetSettingsPolicy | null> {
  try {
    const saved = await ctx.client.applySettings(
      update.revision,
      "updates" in update ? update.updates : [{ path: update.path, value: update.value }],
    );
    const message =
      "updates" in update
        ? `Saved ${update.updates.length} settings changes to config/fleet.toml. Restart Fleet to apply.`
        : `Saved ${update.path} to config/fleet.toml. Restart Fleet to apply.`;
    notifySuccess(ctx, message);
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

/**
 * Parses a hidden Skill reference containing a UUID and version.
 *
 * @param reference - The UUID-and-version reference to parse
 * @returns The pending Skill selection, or `null` if the reference is invalid
 */
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

/**
 * Pins a Skill for the next Turn or updates its existing pending selection.
 *
 * @param selection - The Skill selection to pin or update
 */
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

/**
 * Formats a setting value for display.
 *
 * @param value - The setting value to format
 * @returns `(unset)` for nullish values; otherwise, the JSON representation of the value
 */
function formatSettingValue(value: unknown): string {
  if (value === undefined || value === null) return "(unset)";
  return JSON.stringify(value) ?? "(unset)";
}
