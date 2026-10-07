/** Interactive multi-select picker for Skill pinning on the next Turn. */

import { fuzzyFilter, matchesKey } from "@earendil-works/pi-tui";

import type { FleetSkillCard } from "../../fleet-api-client.js";
import { MARKS } from "../marks.js";
import { MAX_PENDING_SKILLS, type PendingSkillSelection } from "../store.js";
import { selectTheme, theme } from "../theme.js";

import { FilterableListOverlay } from "./list-overlay.js";
import { overlayHint, overlayRule, overlayTitle } from "./overlay.js";

const SKILL_SELECTOR_PAGE_SIZE = 8;

export class SkillSelector extends FilterableListOverlay<FleetSkillCard> {
  private selected: PendingSkillSelection[];

  constructor(
    private readonly skills: FleetSkillCard[],
    current: PendingSkillSelection[],
    private readonly finish: (value: PendingSkillSelection[] | null) => void,
  ) {
    super();
    this.selected = [...current];
  }

  protected allItems(): readonly FleetSkillCard[] {
    return this.skills;
  }

  protected pageSize(): number {
    return SKILL_SELECTOR_PAGE_SIZE;
  }

  protected filterItems(items: readonly FleetSkillCard[], query: string): FleetSkillCard[] {
    const trimmed = query.trim();
    if (!trimmed) return [...items];
    return fuzzyFilter([...items], trimmed, (skill) => `${skill.name} ${skill.description}`);
  }

  protected row(skill: FleetSkillCard): string {
    const pinned = this.selected.some((item) => item.id === skill.id);
    // Filled/empty squares carry state by shape, independent of the theme's colors.
    const marker = pinned ? theme.fg("success", MARKS.checked) : theme.fg("dim", MARKS.unchecked);
    const version = skill.version ? `@${skill.version}` : "";
    const description = skill.name === "No matching Skills" ? "" : skill.description;
    return `${marker} ${skill.name}${version}  ${selectTheme.description(description)}`;
  }

  protected footer(): ReadonlyArray<readonly [string, string]> {
    return [
      ["CTRL+SPACE", "toggle"],
      ["ENTER", "apply"],
      ["ESC", "cancel"],
    ];
  }

  protected filterPlaceholder(): string {
    return "(type to search)";
  }

  protected emptyLabel(): string {
    return "No matching Skills.";
  }

  protected handleKey(data: string, skill: FleetSkillCard | undefined): boolean {
    // Ctrl+Space (not bare Space) so multi-word queries can be typed.
    if (!matchesKey(data, "ctrl+space") || !skill) return false;
    const exists = this.selected.some((item) => item.id === skill.id);
    if (exists) this.selected = this.selected.filter((item) => item.id !== skill.id);
    else if (this.selected.length < MAX_PENDING_SKILLS)
      this.selected.push({
        id: skill.id,
        expectedVersion: skill.version,
        displayName: skill.name,
      });
    return true;
  }

  protected confirm(): void {
    this.finish(this.selected);
  }

  protected cancel(): void {
    this.finish(null);
  }

  /** The pending count leads the shared page status. */
  protected renderStatus(): string {
    return `${this.selected.length}/${MAX_PENDING_SKILLS} selected · ${super.renderStatus()}`;
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const lines = [
      overlayTitle("Skills for the next Turn"),
      overlayHint("Pin exact Skill versions for the next accepted Turn"),
      overlayRule(safeWidth),
      this.renderFilter(),
      "",
      ...this.renderRows(),
      "",
      selectTheme.scrollInfo(this.renderStatus()),
      overlayRule(safeWidth),
      this.renderFooter(),
    ];
    return this.clip(lines, safeWidth);
  }
}
