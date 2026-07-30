import {
  decodeKittyPrintable,
  SelectList,
  matchesKey,
  truncateToWidth,
  type Component,
  type Editor,
  type TUI,
} from "@earendil-works/pi-tui";

import type { FleetSession, FleetSettingsPolicy, FleetSkillCard } from "../fleet-api-client.js";
import type { CommandPresenter, CommandSpec, SettingsUpdate } from "./commands.js";
import type { ConversationStore, PendingSkillSelection } from "./store.js";
import { selectTheme, theme } from "./theme.js";

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
      const selector = new SettingsSelector(settings, (value) => {
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
    const query = this.query.trim().toLocaleLowerCase();
    if (!query) return this.skills;
    return this.skills.filter((skill) =>
      `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(query),
    );
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
type SelectorPage = "scopes" | "fields" | "edit" | "confirm";

/** Keyboard-only hierarchical settings selector and scalar editor. */
export class SettingsSelector implements Component {
  private page: SelectorPage = "scopes";
  private scopeIndex = 0;
  private fieldIndex = 0;
  private choiceIndex = 0;
  private selectedChoices: string[] = [];
  private text = "";

  constructor(
    private readonly settings: FleetSettingsPolicy,
    private readonly finish: (value: SettingsUpdate | null) => void,
  ) {}

  invalidate(): void {}

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const lines =
      this.page === "scopes"
        ? this.renderScopes()
        : this.page === "fields"
          ? this.renderFields()
          : this.page === "edit"
            ? this.renderEditor()
            : this.renderConfirmation();
    return lines.map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    if (this.page === "scopes") this.handleScopes(data);
    else if (this.page === "fields") this.handleFields(data);
    else if (this.page === "edit") this.handleEditor(data);
    else this.handleConfirmation(data);
  }

  private renderScopes(): string[] {
    return [
      theme.fg("accent", theme.bold("Fleet settings (select a policy scope)")),
      theme.fg("muted", "Enter open · Escape cancel · saved settings require restart"),
      "",
      ...this.settings.scopes.map((scope, index) =>
        this.row(index, scope.name, `${scope.fields.length} settings`),
      ),
    ];
  }

  private renderFields(): string[] {
    const scope = this.scope();
    return [
      theme.fg("accent", theme.bold(`[${scope.name}] settings`)),
      theme.fg("muted", "Enter edit · Escape back"),
      "",
      ...scope.fields.map((field, index) =>
        this.row(index, `${field.group} · ${field.label}`, displayValue(field.value)),
      ),
    ];
  }

  private renderEditor(): string[] {
    const field = this.field();
    const current = displayValue(field.value);
    const header = theme.fg("accent", theme.bold(`${field.group} · ${field.label}`));
    if (field.editor === "boolean") {
      return [
        header,
        theme.fg("muted", `Current: ${current} · Enter choose · Escape back`),
        "",
        ...["true", "false"].map((choice, index) => this.row(index, choice, "")),
      ];
    }
    if (field.editor === "single_choice" || field.editor === "multi_choice") {
      const instruction =
        field.editor === "multi_choice"
          ? "Space toggle · Enter choose · Escape back"
          : "Enter choose · Escape back";
      return [
        header,
        theme.fg("muted", `Current: ${current} · ${instruction}`),
        "",
        ...choicesOf(field).map((choice, index) => {
          const label =
            field.editor === "multi_choice"
              ? `[${this.selectedChoices.includes(choice) ? "x" : " "}] ${choice}`
              : choice;
          return this.row(index, label, "");
        }),
      ];
    }
    return [
      header,
      theme.fg("muted", `Current: ${current} · Enter continue · Escape back`),
      "",
      `${theme.fg("muted", "New value:")} ${this.text || theme.fg("dim", "(type a value)")}`,
    ];
  }

  private renderConfirmation(): string[] {
    const field = this.field();
    return [
      theme.fg("warning", theme.bold("Save Fleet setting?")),
      "",
      `${field.path}: ${displayValue(field.value)} → ${displayValue(this.editedValue())}`,
      "",
      theme.fg("muted", "Enter save · Escape return to editor"),
    ];
  }

  private handleScopes(data: string): void {
    if (matchesKey(data, "up")) this.scopeIndex = Math.max(0, this.scopeIndex - 1);
    else if (matchesKey(data, "down"))
      this.scopeIndex = Math.min(this.settings.scopes.length - 1, this.scopeIndex + 1);
    else if (matchesKey(data, "enter")) {
      this.fieldIndex = 0;
      this.page = "fields";
    } else if (matchesKey(data, "escape")) this.finish(null);
  }

  private handleFields(data: string): void {
    const fields = this.scope().fields;
    if (matchesKey(data, "up")) this.fieldIndex = Math.max(0, this.fieldIndex - 1);
    else if (matchesKey(data, "down"))
      this.fieldIndex = Math.min(fields.length - 1, this.fieldIndex + 1);
    else if (matchesKey(data, "enter")) {
      const field = this.field();
      this.choiceIndex = Math.max(0, choicesOf(field).indexOf(String(field.value)));
      this.selectedChoices = Array.isArray(field.value)
        ? field.value.filter((value): value is string => typeof value === "string")
        : [];
      this.text = field.editor === "text" || field.editor === "number" ? String(field.value) : "";
      this.page = "edit";
    } else if (matchesKey(data, "escape")) this.page = "scopes";
  }

  private handleEditor(data: string): void {
    const field = this.field();
    if (matchesKey(data, "escape")) {
      this.page = "fields";
      return;
    }
    if (
      field.editor === "boolean" ||
      field.editor === "single_choice" ||
      field.editor === "multi_choice"
    ) {
      const choices = field.editor === "boolean" ? ["true", "false"] : choicesOf(field);
      if (matchesKey(data, "up")) this.choiceIndex = Math.max(0, this.choiceIndex - 1);
      else if (matchesKey(data, "down"))
        this.choiceIndex = Math.min(choices.length - 1, this.choiceIndex + 1);
      else if (field.editor === "multi_choice" && data === " ") {
        const choice = choices[this.choiceIndex];
        if (!choice) return;
        this.selectedChoices = this.selectedChoices.includes(choice)
          ? this.selectedChoices.filter((item) => item !== choice)
          : [...this.selectedChoices, choice];
      } else if (matchesKey(data, "enter")) this.page = "confirm";
      return;
    }
    if (matchesKey(data, "backspace")) {
      this.text = Array.from(this.text).slice(0, -1).join("");
    } else if (matchesKey(data, "enter")) {
      if (this.text.trim()) this.page = "confirm";
    } else {
      const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
      if (printable) this.text += printable;
    }
  }

  private handleConfirmation(data: string): void {
    if (matchesKey(data, "escape")) {
      this.page = "edit";
      return;
    }
    if (!matchesKey(data, "enter")) return;
    this.finish({
      revision: this.settings.revision,
      scope: this.scope().name,
      path: this.field().path,
      value: this.editedValue(),
    });
  }

  private scope(): FleetSettingsPolicy["scopes"][number] {
    return this.settings.scopes[this.scopeIndex] ?? { name: "defaults", fields: [] };
  }

  private field(): SettingsField {
    return (
      this.scope().fields[this.fieldIndex] ?? {
        path: "",
        group: "",
        label: "",
        value: "",
        editor: "text",
        choices: [],
        environment_overridden: false,
      }
    );
  }

  private editedValue(): string | number | boolean | string[] | null {
    const field = this.field();
    if (field.editor === "boolean") return this.choiceIndex === 0;
    if (field.editor === "single_choice") return choicesOf(field)[this.choiceIndex] ?? "";
    if (field.editor === "multi_choice") return this.selectedChoices;
    if (field.editor === "number") return Number(this.text);
    return this.text;
  }

  private row(index: number, label: string, description: string): string {
    const selected =
      (this.page === "scopes" && index === this.scopeIndex) ||
      (this.page === "fields" && index === this.fieldIndex) ||
      (this.page === "edit" && index === this.choiceIndex);
    const content = description ? `${label}  ${selectTheme.description(description)}` : label;
    return `${selected ? selectTheme.selectedPrefix(">") : " "} ${selected ? selectTheme.selectedText(content) : content}`;
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
