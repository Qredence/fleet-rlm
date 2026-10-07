/**
 * Interactive presenter for slash commands and the narrow compatibility
 * facade for the presenter modules: overlay scaffolding lives in
 * `presenter/overlay.ts`, shared list mechanics in `presenter/list-overlay.ts`,
 * settings editors in `presenter/settings.ts`, and the list pickers in
 * `presenter/{skill-selector,session-browser}.ts`.
 */

import {
  type Editor,
  type SelectItem,
  type SettingItem,
  SettingsList,
  type TUI,
} from "@earendil-works/pi-tui";

import type {
  FleetApiClient,
  FleetSession,
  FleetSettingsPolicy,
  FleetSkillCard,
} from "../fleet-api-client.js";
import type {
  CommandPresenter,
  CommandSpec,
  SettingsBatchUpdate,
  SettingsSaveCallback,
  SettingsUpdate,
} from "./commands/registry.js";
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
import { SessionBrowserOverlay } from "./presenter/session-browser.js";
import { SkillSelector } from "./presenter/skill-selector.js";
import { isBusy, type ConversationStore, type PendingSkillSelection } from "./store.js";
import { settingsListTheme } from "./theme.js";

export { SelectOverlay } from "./presenter/overlay.js";
export { ModalSurface } from "./presenter/overlay.js";
export {
  fieldItem,
  MultiChoiceEditor,
  parseFieldValue,
  TextSettingEditor,
} from "./presenter/settings.js";
export { SessionBrowserOverlay } from "./presenter/session-browser.js";
export { SkillSelector } from "./presenter/skill-selector.js";

export class PiCommandPresenter implements CommandPresenter {
  constructor(
    private readonly ui: TUI,
    private readonly editor: Editor,
    private readonly store: ConversationStore,
    private readonly client: FleetApiClient,
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

  async openSessionBrowser(sessions: FleetSession[], total?: number): Promise<string | null> {
    if (isBusy(this.store.getState().run)) return null;
    return new Promise((resolve) => {
      const browser = new SessionBrowserOverlay(
        sessions,
        this.client,
        (result) => {
          handle.hide();
          this.restoreFocus();
          resolve(result.action === "resume" ? result.id : null);
        },
        () => {
          this.ui.requestRender();
        },
        total,
        (updated) => {
          const current = this.store.getState().session;
          if (current?.id !== updated.id) return;
          this.store.dispatch({
            type: "session/init",
            session: {
              id: updated.id,
              title: updated.title,
              status: updated.status,
              resumed: current.resumed,
            },
          });
        },
      );
      const handle = this.showModal(browser);
    });
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

  /** Resolve one field edit when no save callback is supplied. */
  private chooseSettingOnce(settings: FleetSettingsPolicy): Promise<SettingsUpdate | null> {
    return new Promise((resolve) => {
      const finish = (update: SettingsUpdate | null) => {
        handle.hide();
        this.restoreFocus();
        resolve(update);
      };
      const groupItems = (): SettingItem[] =>
        settingGroups(settings).map((group) => ({
          id: group.name,
          label: group.name,
          description: `${group.fields.length} setting${group.fields.length === 1 ? "" : "s"}`,
          currentValue: "",
          submenu: (_current, done) =>
            new SettingsList(
              group.fields.map((field) => fieldItem(field)),
              10,
              settingsListTheme,
              (id, value) => {
                const field = settings.fields.find((candidate) => candidate.path === id);
                if (field) applyFieldValue(settings, field, value, finish);
              },
              () => done(undefined),
              { enableSearch: true },
            ),
        }));
      const handle = this.showModal(
        new TitledComponent(
          new SettingsList(
            groupItems(),
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
      let activeGroupName: string | null = null;
      let applyInFlight = false;
      let root: SettingsList;

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
        left.path === right.path && JSON.stringify(left.value) === JSON.stringify(right.value);

      const resyncDisplayedValues = (): void => {
        if (!activeFieldList) return;
        for (const field of policy.fields.filter((field) => field.group === activeGroupName)) {
          const pending = draft.get(field.path);
          activeFieldList.updateValue(field.path, displayValue(pending?.value ?? field.value));
        }
      };

      const stage = (field: SettingsField, raw: string): void => {
        const parsed = parseFieldValue(field, raw);
        if (!parsed.ok) {
          this.notify(`${field.path}: ${parsed.error}`);
          resyncDisplayedValues();
          return;
        }
        draft.set(field.path, { path: field.path, value: parsed.value });
        updateRootStatus();
        resyncDisplayedValues();
      };

      const onFieldChange = (id: string, raw: string): void => {
        const field = policy.fields.find((candidate) => candidate.path === id);
        if (!field || field.environment_overridden) return;
        stage(field, raw);
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
            const field = refreshed.fields.find((candidate) => candidate.path === update.path);
            return JSON.stringify(field?.value) === JSON.stringify(update.value);
          });
          policy = refreshed;
          if (applied) {
            for (const update of updates) {
              const key = update.path;
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
          ...settingGroups(settings).map((group) => ({
            id: group.name,
            label: group.name,
            description: `${group.fields.length} setting${group.fields.length === 1 ? "" : "s"}`,
            currentValue: "",
            submenu: (_current: string, done: (selectedValue?: string) => void) => {
              const latest = policy.fields.filter((field) => field.group === group.name);
              const fieldList = new SettingsList(
                latest.map((field) =>
                  fieldItem({
                    ...field,
                    value: draft.get(field.path)?.value ?? field.value,
                  }),
                ),
                10,
                settingsListTheme,
                (id, value) => {
                  onFieldChange(id, value);
                },
                () => done(undefined),
                { enableSearch: true },
              );
              activeFieldList = fieldList;
              activeGroupName = group.name;
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

/** Group the single policy's fields by their existing editor categories. */
function settingGroups(
  policy: FleetSettingsPolicy,
): Array<{ name: string; fields: SettingsField[] }> {
  const groups = new Map<string, SettingsField[]>();
  for (const field of policy.fields) {
    const fields = groups.get(field.group) ?? [];
    fields.push(field);
    groups.set(field.group, fields);
  }
  return [...groups].map(([name, fields]) => ({ name, fields }));
}
