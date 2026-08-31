/** Interactive multi-select picker for Skill pinning on the next Turn. */

import {
  type Component,
  decodeKittyPrintable,
  fuzzyFilter,
  matchesKey,
  truncateToWidth,
} from "@earendil-works/pi-tui";

import type { FleetSkillCard } from "../../fleet-api-client.js";
import { MAX_PENDING_SKILLS, type PendingSkillSelection } from "../store.js";
import { dropLastGrapheme } from "../terminal-text.js";
import { selectTheme, theme } from "../theme.js";

import { isPrintableInput, overlayHint, overlayRule, overlayTitle } from "./overlay.js";

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
    const maxVisible = 8;
    const start = Math.max(0, Math.min(this.index - maxVisible + 1, filtered.length - maxVisible));
    const visible = filtered.slice(start, start + maxVisible);
    return [
      overlayTitle("Skills for the next Turn"),
      overlayHint("Pin exact Skill versions for the next accepted Turn"),
      overlayRule(safeWidth),
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
        `${this.selected.length}/${MAX_PENDING_SKILLS} selected · ${filtered.length} shown${filtered.length > maxVisible ? ` · rows ${start + 1}-${Math.min(start + maxVisible, filtered.length)}` : ""}`,
      ),
      overlayRule(safeWidth),
      `${theme.fg("accent", "SPACE")} ${overlayHint("toggle  ·  Enter apply  ·  Esc cancel")}`,
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
      else if (this.selected.length < MAX_PENDING_SKILLS)
        this.selected.push({
          id: skill.id,
          expectedVersion: skill.version,
          displayName: skill.name,
        });
    } else if (matchesKey(data, "backspace")) {
      this.query = dropLastGrapheme(this.query);
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
