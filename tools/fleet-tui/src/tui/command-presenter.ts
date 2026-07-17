import {
  SelectList,
  Text,
  matchesKey,
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
    const text = new Text(
      `Fleet commands\n\n${commands.map((command) => `${command.usage}  ${command.description}`).join("\n")}\n\nEscape closes`,
      1,
      1,
    );
    let close = (_data: string) => {};
    const component: Component = {
      render: (width) => text.render(width),
      invalidate: () => text.invalidate(),
      handleInput: (data) => close(data),
    };
    const handle = this.ui.showOverlay(component, {
      width: "80%",
      maxHeight: "80%",
      anchor: "center",
    });
    close = (data: string) => {
      if (data === "\x1b" || data === "q") {
        handle.hide();
        this.restoreFocus();
      }
    };
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

class SkillSelector implements Component {
  private index = 0;
  private selected: PendingSkillSelection[];
  constructor(
    private readonly skills: FleetSkillCard[],
    current: PendingSkillSelection[],
    private readonly finish: (value: PendingSkillSelection[] | null) => void,
  ) {
    this.selected = [...current];
  }
  invalidate(): void {}
  render(): string[] {
    return [
      "Skills for the next Turn (Space toggle · Enter apply · Escape cancel)",
      "",
      ...this.skills.map((skill, index) => {
        const checked = this.selected.some((item) => item.id === skill.id) ? "x" : " ";
        return `${index === this.index ? ">" : " "} [${checked}] ${skill.name}@${skill.version}  ${skill.description}`;
      }),
      "",
      `${this.selected.length}/4 selected`,
    ];
  }
  handleInput(data: string): void {
    if (matchesKey(data, "up")) this.index = Math.max(0, this.index - 1);
    else if (matchesKey(data, "down"))
      this.index = Math.min(this.skills.length - 1, this.index + 1);
    else if (data === " ") {
      const skill = this.skills[this.index];
      if (!skill) return;
      const exists = this.selected.some((item) => item.id === skill.id);
      if (exists) this.selected = this.selected.filter((item) => item.id !== skill.id);
      else if (this.selected.length < 4)
        this.selected.push({
          id: skill.id,
          expectedVersion: skill.version,
          displayName: skill.name,
        });
    } else if (matchesKey(data, "enter")) this.finish(this.selected);
    else if (matchesKey(data, "escape")) this.finish(null);
  }
}
