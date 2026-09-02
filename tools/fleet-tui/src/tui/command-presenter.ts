/**
 * Interactive presenter for slash commands and the narrow compatibility
 * facade for the presenter modules: overlay scaffolding lives in
 * `presenter/overlay.ts`, settings editors in `presenter/settings.ts`, and
 * the Skill picker in `presenter/skill-selector.ts`.
 */

import {
  type Editor,
  type SelectItem,
  type SettingItem,
  SettingsList,
  type TUI,
} from "@earendil-works/pi-tui";

import type { FleetSession, FleetSettingsPolicy, FleetSkillCard } from "../fleet-api-client.js";
import type {
  CommandPresenter,
  CommandSpec,
  SettingsBatchUpdate,
  SettingsSaveCallback,
  SettingsUpdate,
} from "./commands/registry.js";
import { shortId } from "./format.js";
import {
  ModalSurface,
  OVERLAY_OPTIONS,
  SelectOverlay,
  TitledComponent,
} from "./presenter/overlay.js";
import {
  applyFieldValue,
  displayValue,
  fieldItem,
  parseFieldValue,
  type SettingsField,
} from "./presenter/settings.js";
import { SkillSelector } from "./presenter/skill-selector.js";
import type { ConversationStore, PendingSkillSelection } from "./store.js";
import { settingsListTheme } from "./theme.js";

export { SelectOverlay } from "./presenter/overlay.js";
export { ModalSurface } from "./presenter/overlay.js";
export {
  fieldItem,
  MultiChoiceEditor,
  parseFieldValue,
  TextSettingEditor,
} from "./presenter/settings.js";
export { SkillSelector } from "./presenter/skill-selector.js";

export class PiCommandPresenter implements CommandPresenter {
  constructor(
    private readonly ui: TUI,
    private readonly editor: Editor,
    private readonly store: ConversationStore,
    /** Transient one-shot notice (alt-screen flash); a no-op outside the TUI. */
    private readonly notify: (message: string) => void = () => undefined,
  ) {}

  private restoreFocus = (): void => {
    this.ui.setFocus(this.editor);
  };

  showHelp(commands: CommandSpec[]): void {
    const overlay = new SelectOverlay(
      commands.map((command) => ({
        value: command.name,
        label: command.usage,
        description: command.description,
      })),
      {
        title: "Fleet TUI commands",
        context: `${commands.length} commands · Ctrl+Shift+F search`,
        hint: "Type to filter · ↑↓ navigate · Enter insert",
        filterable: true,
        maxVisible: 8,
      },
    );
    const handle = this.showModal(overlay);
    const finish = (command?: string) => {
      handle.hide();
      if (command) this.editor.setText(`/${command} `);
      this.restoreFocus();
    };
    overlay.onSelect = (item) => finish(item.value);
    overlay.onCancel = () => finish();
  }

  async chooseSession(sessions: FleetSession[]): Promise<string | null> {
    const state = this.store.getState();
    if (["submitting", "running", "cancelling"].includes(state.run.phase)) return null;
    return this.choose(
      sessions.map((session) => ({
        value: session.id,
        label: session.title,
        description: `${relativeUpdatedAt(session.updated_at)} · ${shortId(session.id)}`,
      })),
      {
        title: "Switch Fleet Session",
        hint: "Enter resume",
        selectedValue: state.session?.id,
      },
    );
  }

  async chooseSkills(
    skills: FleetSkillCard[],
    current: PendingSkillSelection[],
  ): Promise<PendingSkillSelection[] | null> {
    return new Promise((resolve) => {
      const selector = new SkillSelector(skills, current, (value) => {
        handle.hide();
        this.restoreFocus();
        resolve(value);
      });
      const handle = this.showModal(selector);
    });
  }

  async chooseSetting(
    settings: FleetSettingsPolicy,
    save?: SettingsSaveCallback,
  ): Promise<SettingsUpdate | null> {
    if (save) {
      await this.editSettingsInteractively(settings, save);
      return null;
    }
    return this.chooseSettingOnce(settings);
  }

  async chooseTheme(themes: string[], current: string | undefined): Promise<string | null> {
    return this.choose(
      themes.map((name) => ({
        value: name,
        label: name === current ? `${name} (current)` : name,
        description: name === current ? "active theme" : "select to apply",
      })),
      {
        title: "Select theme",
        context: `Current: ${current ?? "—"}`,
        hint: "Type to filter · Enter apply",
        filterable: true,
        selectedValue: current,
      },
    );
  }

  async chooseProfile(
    profiles: string[],
    active: string | undefined,
    selected: string | undefined,
  ): Promise<string | null> {
    const contextParts: string[] = [];
    if (active) contextParts.push(`Running: ${active}`);
    if (selected) contextParts.push(`Selected for restart: ${selected}`);
    return this.choose(
      profiles.map((profile) => {
        const isActive = profile === active;
        const isSelected = profile === selected;
        let state: "current" | "running" | "selected" | null = null;
        if (isActive && isSelected) {
          state = "current";
        } else if (isActive) {
          state = "running";
        } else if (isSelected) {
          state = "selected";
        }
        let description = "select for next restart";
        if (state === "current") {
          description = "running and selected for restart";
        } else if (state === "running") {
          description = "running now; select to keep on restart";
        } else if (state === "selected") {
          description = "applies on restart";
        }
        return {
          value: profile,
          label: state ? `${profile} (${state})` : profile,
          description,
        };
      }),
      {
        title: "Select profile for next restart",
        context: contextParts.join(" · ") || undefined,
        hint: "Enter select",
        selectedValue: selected,
      },
    );
  }

  private choose(
    items: SelectItem[],
    options: {
      title: string;
      context?: string;
      hint?: string;
      filterable?: boolean;
      selectedValue?: string;
    },
  ): Promise<string | null> {
    return new Promise((resolve) => {
      const overlay = new SelectOverlay(items, options);
      const handle = this.showModal(overlay);
      const finish = (value: string | null) => {
        handle.hide();
        this.restoreFocus();
        resolve(value);
      };
      overlay.onSelect = (item) => finish(item.value);
      overlay.onCancel = () => finish(null);
    });
  }

  /** Legacy contract: resolve the first chosen update and close (mocks/tests). */
  private chooseSettingOnce(settings: FleetSettingsPolicy): Promise<SettingsUpdate | null> {
    return new Promise((resolve) => {
      const finish = (update: SettingsUpdate | null) => {
        handle.hide();
        this.restoreFocus();
        resolve(update);
      };
      const scopeItems = (): SettingItem[] =>
        settings.scopes.map((scope) => ({
          id: scope.name,
          label: scope.name,
          description: `${scope.fields.length} setting${scope.fields.length === 1 ? "" : "s"}`,
          currentValue: "",
          submenu: (_current, done) =>
            new SettingsList(
              scope.fields.map((field) => fieldItem(field)),
              10,
              settingsListTheme,
              (id, value) => {
                const field = settings.scopes
                  .flatMap((item) => item.fields)
                  .find((candidate) => candidate.path === id);
                if (field) applyFieldValue(settings, scope.name, field, value, finish);
              },
              () => done(undefined),
              { enableSearch: true },
            ),
        }));
      const handle = this.showModal(
        new TitledComponent(
          new SettingsList(
            scopeItems(),
            10,
            settingsListTheme,
            () => undefined,
            () => finish(null),
            { enableSearch: true },
          ),
          "Fleet settings",
          "Saved changes apply after a Fleet restart",
        ),
      );
    });
  }

  /** Settings edits stay local until the operator explicitly applies one batch. */
  private editSettingsInteractively(
    settings: FleetSettingsPolicy,
    save: SettingsSaveCallback,
  ): Promise<void> {
    return new Promise((resolve) => {
      let policy = settings;
      const draft = new Map<string, SettingsBatchUpdate["updates"][number]>();
      let activeFieldList: SettingsList | null = null;
      let activeScopeName: string | null = null;
      let applyInFlight = false;
      let root: SettingsList;

      const draftKey = (scope: string, path: string): string => `${scope}\u0000${path}`;
      const updateRootStatus = (): void => {
        let status = "no changes";
        if (applyInFlight) status = "applying...";
        else if (draft.size) status = `${draft.size} pending`;
        root.updateValue("__apply", status);
        root.updateValue("__discard", draft.size ? `${draft.size} pending` : "no changes");
      };

      const sameDraftUpdate = (
        left: SettingsBatchUpdate["updates"][number],
        right: SettingsBatchUpdate["updates"][number],
      ): boolean =>
        left.scope === right.scope &&
        left.path === right.path &&
        left.unset === right.unset &&
        JSON.stringify(left.value) === JSON.stringify(right.value);

      const resyncDisplayedValues = (): void => {
        // Field paths are unique per scope list; only the open scope's list
        // can accept updates, so refresh from its fields alone.
        const activeScope = policy.scopes.find((scope) => scope.name === activeScopeName);
        if (!activeFieldList || !activeScope) return;
        for (const field of activeScope.fields) {
          const pending = draft.get(draftKey(activeScope.name, field.path));
          activeFieldList.updateValue(
            field.path,
            displayValue(pending?.unset ? field.value : (pending?.value ?? field.value)),
          );
        }
      };

      const stage = (scopeName: string, field: SettingsField, raw: string): void => {
        const parsed = parseFieldValue(field, raw);
        if (!parsed.ok) {
          this.notify(`${field.path}: ${parsed.error}`);
          resyncDisplayedValues();
          return;
        }
        draft.set(draftKey(scopeName, field.path), {
          scope: scopeName,
          path: field.path,
          value: parsed.value,
        });
        updateRootStatus();
        resyncDisplayedValues();
      };

      const onFieldChange = (scopeName: string, id: string, raw: string): void => {
        const scope = policy.scopes.find((candidate) => candidate.name === scopeName);
        const field = scope?.fields.find((candidate) => candidate.path === id);
        if (!field || field.environment_overridden) return;
        stage(scopeName, field, raw);
      };

      const applyDraft = async (): Promise<void> => {
        if (applyInFlight) {
          this.notify("A settings apply is already in progress.");
          return;
        }
        if (!draft.size) {
          this.notify("No settings changes to apply.");
          return;
        }
        const updates = [...draft.values()];
        applyInFlight = true;
        updateRootStatus();
        try {
          const refreshed = await save({ revision: policy.revision, updates });
          if (!refreshed) return;
          const applied = updates.every((update) => {
            const field = refreshed.scopes
              .find((scope) => scope.name === update.scope)
              ?.fields.find((candidate) => candidate.path === update.path);
            return update.unset
              ? field?.origin === "inherited"
              : JSON.stringify(field?.value) === JSON.stringify(update.value);
          });
          policy = refreshed;
          if (applied) {
            for (const update of updates) {
              const key = draftKey(update.scope, update.path);
              const current = draft.get(key);
              if (current && sameDraftUpdate(current, update)) draft.delete(key);
            }
          } else {
            this.notify(
              "Settings changed outside this TUI; review the refreshed policy and reapply the draft.",
            );
          }
        } finally {
          applyInFlight = false;
          updateRootStatus();
          resyncDisplayedValues();
        }
      };

      const discardDraft = (): void => {
        draft.clear();
        updateRootStatus();
        resyncDisplayedValues();
        this.notify("Discarded pending settings changes.");
      };

      root = new SettingsList(
        [
          ...settings.scopes.map((scope) => ({
            id: scope.name,
            label: scope.name,
            description: `${scope.fields.length} setting${scope.fields.length === 1 ? "" : "s"}`,
            currentValue: "",
            submenu: (_current: string, done: (selectedValue?: string) => void) => {
              // Look up the freshest snapshot so reopening a scope reflects
              // values saved or refreshed through the server.
              const latest =
                policy.scopes.find((candidate) => candidate.name === scope.name) ?? scope;
              const fieldList = new SettingsList(
                latest.fields.flatMap((field) => [
                  fieldItem({
                    ...field,
                    value: draft.get(draftKey(scope.name, field.path))?.value ?? field.value,
                  }),
                  ...(field.can_reset
                    ? [
                        {
                          id: `__reset:${field.path}`,
                          label: `Reset ${field.label}`,
                          description: "Remove this profile override and inherit the default value",
                          currentValue: "",
                          values: ["reset"],
                        },
                      ]
                    : []),
                ]),
                10,
                settingsListTheme,
                (id, value) => {
                  if (id.startsWith("__reset:")) {
                    const path = id.slice("__reset:".length);
                    draft.set(draftKey(scope.name, path), { scope: scope.name, path, unset: true });
                    updateRootStatus();
                    resyncDisplayedValues();
                    return;
                  }
                  onFieldChange(scope.name, id, value);
                },
                () => done(undefined),
                { enableSearch: true },
              );
              activeFieldList = fieldList;
              activeScopeName = scope.name;
              return fieldList;
            },
          })),
          {
            id: "__apply",
            label: "Apply draft",
            description: "Validate and write all pending changes atomically",
            currentValue: "no changes",
            values: ["apply"],
          },
          {
            id: "__discard",
            label: "Discard draft",
            description: "Restore the server policy values in this editor",
            currentValue: "no changes",
            values: ["discard"],
          },
        ],
        10,
        settingsListTheme,
        (id) => {
          if (id === "__apply") void applyDraft();
          if (id === "__discard") discardDraft();
        },
        () => {
          handle.hide();
          this.restoreFocus();
          resolve();
        },
        { enableSearch: true },
      );
      const handle = this.showModal(
        new TitledComponent(
          root,
          "Fleet settings",
          "Enter edit · Apply draft to save · Esc back/close · restart Fleet after saving",
        ),
      );
      updateRootStatus();
    });
  }

  /** Mount every presenter flow in the same pi-tui focusable modal surface. */
  private showModal(component: import("@earendil-works/pi-tui").Component) {
    return this.ui.showOverlay(new ModalSurface(component), OVERLAY_OPTIONS);
  }
}

/**
 * Formats a timestamp as a relative update label.
 *
 * @param value - The timestamp to format, or `null` or `undefined`
 * @returns A relative update label, or `updated —` for a missing or invalid timestamp
 */
function relativeUpdatedAt(value: string | null | undefined): string {
  if (!value) return "updated —";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "updated —";
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (elapsedMinutes < 1) return "updated now";
  if (elapsedMinutes < 60) return `updated ${elapsedMinutes}m ago`;
  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) return `updated ${elapsedHours}h ago`;
  return `updated ${Math.floor(elapsedHours / 24)}d ago`;
}
