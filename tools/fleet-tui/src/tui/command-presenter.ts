import {
  decodeKittyPrintable,
  fuzzyFilter,
  SelectList,
  SettingsList,
  matchesKey,
  truncateToWidth,
  type Component,
  type Editor,
  type SettingItem,
  type TUI,
} from "@earendil-works/pi-tui";

import type { FleetSession, FleetSettingsPolicy, FleetSkillCard } from "../fleet-api-client.js";
import type { CommandPresenter, CommandSpec, SettingsUpdate } from "./commands.js";
import type { ConversationStore, PendingSkillSelection } from "./store.js";
import { selectTheme, settingsListTheme, theme } from "./theme.js";

/** Renderer-neutral command contract backed by pi-tui overlays. */
export class PiCommandPresenter implements CommandPresenter {
  constructor(
    private readonly ui: TUI,
    private readonly editor: Editor,
    private readonly store: ConversationStore,
  ) {}

  private restoreFocus = (): void => {
    this.ui.setFocus(this.editor);
  };

  showHelp(commands: CommandSpec[]): void {
    const list = new SelectList(
      commands.map((command) => ({
        value: command.usage.split(" ", 1)[0] ?? command.usage,
        label: command.usage,
        description: command.description,
      })),
      12,
      selectTheme,
    );
    const handle = this.ui.showOverlay(list, {
      width: "80%",
      maxHeight: "80%",
      anchor: "center",
    });
    const finish = (command?: string) => {
      handle.hide();
      if (command) this.editor.setText(`${command} `);
      this.restoreFocus();
    };
    list.onSelect = (item) => finish(item.value);
    list.onCancel = () => finish();
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
      const handle = this.ui.showOverlay(selector, {
        width: "80%",
        maxHeight: "80%",
        anchor: "center",
      });
    });
  }

  async chooseSetting(settings: FleetSettingsPolicy): Promise<SettingsUpdate | null> {
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
      const handle = this.ui.showOverlay(
        new SettingsList(
          scopeItems(),
          10,
          settingsListTheme,
          () => undefined,
          () => finish(null),
          { enableSearch: true },
        ),
        { width: "80%", maxHeight: "80%", anchor: "center" },
      );
    });
  }

  async chooseTheme(themes: string[], current: string | undefined): Promise<string | null> {
    return this.choose(
      themes.map((name) => ({
        value: name,
        label: name === current ? `${name} (current)` : name,
        description: name === current ? "active theme" : "select to apply",
      })),
    );
  }

  async chooseProfile(
    profiles: string[],
    active: string | undefined,
    selected: string | undefined,
  ): Promise<string | null> {
    return this.choose(
      profiles.map((profile) => {
        const isActive = profile === active;
        const isSelected = profile === selected;
        const state =
          isActive && isSelected
            ? "current"
            : isActive
              ? "running"
              : isSelected
                ? "selected"
                : null;
        return {
          value: profile,
          label: state ? `${profile} (${state})` : profile,
          description:
            state === "current"
              ? "active and selected"
              : state === "running"
                ? "select to keep on restart"
                : state === "selected"
                  ? "restart to apply"
                  : "select for next restart",
        };
      }),
    );
  }

  private choose(
    items: { value: string; label: string; description?: string }[],
  ): Promise<string | null> {
    return new Promise((resolve) => {
      const list = new SelectList(items, 10, selectTheme);
      const handle = this.ui.showOverlay(list, {
        width: "80%",
        maxHeight: "80%",
        anchor: "center",
      });
      const finish = (value: string | null) => {
        handle.hide();
        this.restoreFocus();
        resolve(value);
      };
      list.onSelect = (item) => finish(item.value);
      list.onCancel = () => finish(null);
    });
  }
}

export class SkillSelector implements Component {
  private index = 0;
  private query = "";
  private selected: PendingSkillSelection[];
  constructor(
    private readonly skills: FleetSkillCard[],
    current: PendingSkillSelection[],
    private readonly finish: (value: PendingSkillSelection[] | null) => void,
  ) {
    this.selected = [...current];
  }
  invalidate(): void {}
  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const filtered = this.filteredSkills();
    this.index = Math.min(this.index, Math.max(0, filtered.length - 1));
    const maxVisible = 10;
    const start = Math.max(0, Math.min(this.index - maxVisible + 1, filtered.length - maxVisible));
    const visible = filtered.slice(start, start + maxVisible);
    return [
      theme.fg(
        "accent",
        theme.bold("Skills for the next Turn (Space toggle · Enter apply · Escape cancel)"),
      ),
      `${theme.fg("muted", "Filter:")} ${this.query || theme.fg("dim", "(type to search)")}`,
      "",
      ...(visible.length > 0
        ? visible
        : [{ id: "", name: "No matching Skills", version: "", description: "" }]
      ).map((skill, offset) => {
        const index = start + offset;
        const checked = this.selected.some((item) => item.id === skill.id) ? "x" : " ";
        const version = skill.version ? `@${skill.version}` : "";
        const label = `[${checked}] ${skill.name}${version}`;
        const selected = index === this.index;
        return `${selected ? selectTheme.selectedPrefix(">") : " "} ${selected ? selectTheme.selectedText(label) : label}  ${selectTheme.description(skill.description)}`;
      }),
      "",
      selectTheme.scrollInfo(
        `${this.selected.length}/4 selected · ${filtered.length} shown${filtered.length > maxVisible ? ` · rows ${start + 1}-${Math.min(start + maxVisible, filtered.length)}` : ""}`,
      ),
    ].map((line) => truncateToWidth(line, safeWidth, "…"));
  }
  handleInput(data: string): void {
    const filtered = this.filteredSkills();
    if (matchesKey(data, "up")) this.index = Math.max(0, this.index - 1);
    else if (matchesKey(data, "down")) this.index = Math.min(filtered.length - 1, this.index + 1);
    else if (matchesKey(data, "pageUp")) this.index = Math.max(0, this.index - 10);
    else if (matchesKey(data, "pageDown"))
      this.index = Math.min(filtered.length - 1, this.index + 10);
    else if (data === " ") {
      const skill = filtered[this.index];
      if (!skill) return;
      const exists = this.selected.some((item) => item.id === skill.id);
      if (exists) this.selected = this.selected.filter((item) => item.id !== skill.id);
      else if (this.selected.length < 4)
        this.selected.push({
          id: skill.id,
          expectedVersion: skill.version,
          displayName: skill.name,
        });
    } else if (matchesKey(data, "backspace")) {
      const graphemes = Array.from(
        new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(this.query),
        ({ segment }) => segment,
      );
      this.query = graphemes.slice(0, -1).join("");
      this.index = 0;
    } else if (matchesKey(data, "enter")) this.finish(this.selected);
    else if (matchesKey(data, "escape")) this.finish(null);
    else {
      const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
      if (printable) {
        this.query += printable;
        this.index = 0;
      }
    }
  }

  private filteredSkills(): FleetSkillCard[] {
    const query = this.query.trim();
    if (!query) return this.skills;
    return fuzzyFilter(this.skills, query, (skill) => `${skill.name} ${skill.description}`);
  }
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

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

type SettingsField = FleetSettingsPolicy["scopes"][number]["fields"][number];

function fieldItem(field: SettingsField): SettingItem {
  const base = {
    id: field.path,
    label: `${field.group} · ${field.label}`,
    description: field.path,
    currentValue: displayValue(field.value),
  };
  if (field.editor === "boolean" || field.editor === "single_choice") {
    return {
      ...base,
      values:
        field.editor === "boolean"
          ? ["false", "true"]
          : choicesOf(field).length > 0
            ? choicesOf(field)
            : [displayValue(field.value)],
    };
  }
  if (field.editor === "multi_choice") {
    return {
      ...base,
      submenu: (_current, done) => new MultiChoiceEditor(field, done),
    };
  }
  return {
    ...base,
    submenu: (_current, done) => new TextSettingEditor(field, done),
  };
}

function applyFieldValue(
  settings: FleetSettingsPolicy,
  scope: string,
  field: SettingsField,
  raw: string,
  finish: (update: SettingsUpdate | null) => void,
): void {
  const value =
    field.editor === "boolean"
      ? raw === "true"
      : field.editor === "number"
        ? Number(raw)
        : field.editor === "multi_choice"
          ? raw.split(",").filter(Boolean)
          : raw;
  finish({
    revision: settings.revision,
    scope,
    path: field.path,
    value,
  });
}

/** Minimal keyboard text editor for text/number settings. */
export class TextSettingEditor implements Component {
  private value: string;

  constructor(
    private readonly field: SettingsField,
    private readonly finish: (value?: string) => void,
  ) {
    this.value = String(field.value);
  }

  invalidate(): void {}

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    return [
      theme.fg("accent", theme.bold(`${this.field.group} · ${this.field.label}`)),
      theme.fg("muted", `Current: ${displayValue(this.field.value)} · Enter save · Escape back`),
      "",
      `${theme.fg("muted", "New value:")} ${this.value || theme.fg("dim", "(type a value)")}`,
    ].map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    if (matchesKey(data, "escape")) {
      this.finish(undefined);
    } else if (matchesKey(data, "enter")) {
      if (this.value.trim()) this.finish(this.value);
    } else if (matchesKey(data, "backspace")) {
      this.value = Array.from(this.value).slice(0, -1).join("");
    } else {
      const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
      if (printable) this.value += printable;
    }
  }
}

/** Space-toggles a multi-choice list; Enter confirms. */
export class MultiChoiceEditor implements Component {
  private index = 0;
  private selected: string[];

  constructor(
    private readonly field: SettingsField,
    private readonly finish: (value?: string) => void,
  ) {
    this.selected = Array.isArray(field.value)
      ? field.value.filter((value): value is string => typeof value === "string")
      : [];
  }

  invalidate(): void {}

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const choices = choicesOf(this.field);
    const lines = [
      theme.fg("accent", theme.bold(`${this.field.group} · ${this.field.label}`)),
      theme.fg("muted", "Space toggle · Enter confirm · Escape back"),
      "",
      ...(choices.length > 0 ? choices : ["(no choices)"]).map((choice, index) => {
        const checked = this.selected.includes(choice) ? "x" : " ";
        const label = `[${checked}] ${choice}`;
        const selected = index === this.index;
        return `${selected ? selectTheme.selectedPrefix(">") : " "} ${selected ? selectTheme.selectedText(label) : label}`;
      }),
    ];
    return lines.map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    const choices = choicesOf(this.field);
    if (matchesKey(data, "up")) this.index = Math.max(0, this.index - 1);
    else if (matchesKey(data, "down")) this.index = Math.min(choices.length - 1, this.index + 1);
    else if (data === " ") {
      const choice = choices[this.index];
      if (!choice) return;
      this.selected = this.selected.includes(choice)
        ? this.selected.filter((item) => item !== choice)
        : [...this.selected, choice];
    } else if (matchesKey(data, "enter")) this.finish(this.selected.join(","));
    else if (matchesKey(data, "escape")) this.finish(undefined);
  }
}

function choicesOf(field: SettingsField): string[] {
  return field.choices ?? [];
}

function displayValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function isPrintableInput(value: string): boolean {
  return (
    value.length > 0 &&
    Array.from(value).every((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint >= 0x20 && codePoint !== 0x7f;
    })
  );
}
