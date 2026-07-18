import {
  decodeKittyPrintable,
  SelectList,
  matchesKey,
  truncateToWidth,
  type Component,
  type Editor,
  type TUI,
} from "@earendil-works/pi-tui";

import type { FleetSession, FleetSkillCard } from "../fleet-api-client.js";
import type { CommandPresenter, CommandSpec } from "./commands.js";
import type { ConversationStore, PendingSkillSelection } from "./store.js";
import { selectTheme } from "./theme.js";

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
    const id = await this.choose(
      sessions.map((session) => ({
        value: session.id,
        label: session.title,
        description: session.id,
      })),
    );
    if (!id) return null;
    const draft = this.editor.getText();
    if (!draft && state.pendingSkillSelections.length === 0) return id;
    const disclosure = `${draft ? "Unsent draft" : ""}${draft && state.pendingSkillSelections.length ? " and " : ""}${state.pendingSkillSelections.length ? `${state.pendingSkillSelections.length} pending Skill selection(s)` : ""}`;
    const confirmed = await this.choose([
      { value: "cancel", label: "Keep current session", description: disclosure },
      { value: "switch", label: "Discard and switch", description: disclosure },
    ]);
    if (confirmed !== "switch") return null;
    this.editor.setText("");
    this.store.dispatch({ type: "skill-selection/clear" });
    return id;
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
      "Skills for the next Turn (Space toggle · Enter apply · Escape cancel)",
      `Filter: ${this.query || "(type to search)"}`,
      "",
      ...(visible.length > 0
        ? visible
        : [{ id: "", name: "No matching Skills", version: "", description: "" }]
      ).map((skill, offset) => {
        const index = start + offset;
        const checked = this.selected.some((item) => item.id === skill.id) ? "x" : " ";
        const version = skill.version ? `@${skill.version}` : "";
        return `${index === this.index ? ">" : " "} [${checked}] ${skill.name}${version}  ${skill.description}`;
      }),
      "",
      `${this.selected.length}/4 selected · ${filtered.length} shown${filtered.length > maxVisible ? ` · rows ${start + 1}-${Math.min(start + maxVisible, filtered.length)}` : ""}`,
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

function isPrintableInput(value: string): boolean {
  return (
    value.length > 0 &&
    Array.from(value).every((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint >= 0x20 && codePoint !== 0x7f;
    })
  );
}
