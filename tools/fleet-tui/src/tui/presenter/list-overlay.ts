/**
 * Shared mechanics for Fleet's keyboard-first filterable list overlays.
 *
 * `SkillSelector` and `SessionBrowserOverlay` render different rows, footers,
 * and actions, but they must not each re-implement the list state machine: the
 * index clamp, the page window, and the navigation/filter key dispatch. This
 * base owns exactly that, so a new overlay supplies data and row rendering and
 * inherits consistent behavior. Session titles and other backend strings remain
 * the subclass's responsibility to sanitize before returning rows.
 */

import {
  type Component,
  decodeKittyPrintable,
  matchesKey,
  truncateToWidth,
} from "@earendil-works/pi-tui";

import { MARKS } from "../marks.js";
import { dropLastGrapheme } from "../terminal-text.js";
import { selectTheme, theme } from "../theme.js";
import { isPrintableInput, overlayFilter, overlayFooter } from "./overlay.js";

/** The filtered list plus the window that keeps `index` visible. */
export type ListWindow<T> = {
  /** All items matching the current query. */
  filtered: T[];
  /** Index into `filtered` of the first visible row. */
  start: number;
  /** The rows to render, in order. */
  visible: T[];
};

export abstract class FilterableListOverlay<T> implements Component {
  protected index = 0;
  protected query = "";

  // --- Subclass contract -----------------------------------------------------

  /** All candidate items, before filtering. */
  protected abstract allItems(): readonly T[];
  /** Rows of the visible page. */
  protected abstract pageSize(): number;
  /** Apply the query to the items. */
  protected abstract filterItems(items: readonly T[], query: string): T[];
  /** Row content (the selection prefix and cursor are added by the base). */
  protected abstract row(item: T, selected: boolean): string;
  /** Key footer entries, as `[key, action]` pairs. */
  protected abstract footer(): ReadonlyArray<readonly [string, string]>;

  /** Enter on the selected item. */
  protected confirm(_item: T): void {}
  /** Escape. */
  protected cancel(): void {}
  /** Non-navigation keys for the selected item; return true when consumed. */
  protected handleKey(_data: string, _item: T | undefined): boolean {
    return false;
  }
  protected filterPlaceholder(): string {
    return "(type to filter)";
  }
  protected emptyLabel(): string {
    return "No matching items.";
  }

  abstract render(width: number): string[];

  invalidate(): void {}

  // --- Shared mechanics ------------------------------------------------------

  /** The current page window, with the index clamped into range. */
  protected window(): ListWindow<T> {
    const filtered = this.filterItems(this.allItems(), this.query);
    this.index = Math.min(this.index, Math.max(0, filtered.length - 1));
    const size = this.pageSize();
    const start = Math.max(0, Math.min(this.index - size + 1, filtered.length - size));
    return { filtered, start, visible: filtered.slice(start, start + size) };
  }

  /** The currently highlighted item, if any. */
  protected current(): T | undefined {
    return this.filterItems(this.allItems(), this.query)[this.index];
  }

  /** The shared filter line. */
  protected renderFilter(): string {
    return overlayFilter(this.query, this.filterPlaceholder());
  }

  /** The shared key footer. */
  protected renderFooter(): string {
    return overlayFooter([...this.footer()]);
  }

  /** The shared page status line, e.g. `3 shown · rows 1-3`. */
  protected renderStatus(): string {
    const { filtered, start, visible } = this.window();
    const range =
      filtered.length > visible.length
        ? ` · rows ${start + 1}-${Math.min(start + visible.length, filtered.length)}`
        : "";
    return `${filtered.length} shown${range}`;
  }

  /** Map the visible page to prefixed, selection-styled rows. */
  protected renderRows(): string[] {
    const { start, visible } = this.window();
    if (visible.length === 0) return [`  ${theme.fg("muted", this.emptyLabel())}`];
    return visible.map((item, offset) => {
      const selected = start + offset === this.index;
      const content = this.row(item, selected);
      const prefix = selected ? selectTheme.selectedPrefix(MARKS.submit) : " ";
      return ` ${prefix} ${selected ? selectTheme.selectedText(content) : content}`;
    });
  }

  /** Truncate `lines` to `width`, replacing the tail with an ellipsis. */
  protected clip(lines: string[], width: number): string[] {
    const safeWidth = Math.max(1, width);
    return lines.map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    const filtered = this.filterItems(this.allItems(), this.query);
    if (matchesKey(data, "up")) {
      this.index = Math.max(0, this.index - 1);
      return;
    }
    if (matchesKey(data, "down")) {
      this.index = Math.min(filtered.length - 1, this.index + 1);
      return;
    }
    if (matchesKey(data, "pageUp")) {
      this.index = Math.max(0, this.index - this.pageSize());
      return;
    }
    if (matchesKey(data, "pageDown")) {
      this.index = Math.min(filtered.length - 1, this.index + this.pageSize());
      return;
    }
    if (matchesKey(data, "enter")) {
      const item = filtered[this.index];
      if (item !== undefined) this.confirm(item);
      return;
    }
    if (matchesKey(data, "escape")) {
      this.cancel();
      return;
    }
    if (this.handleKey(data, filtered[this.index])) return;
    if (matchesKey(data, "backspace")) {
      this.setQuery(dropLastGrapheme(this.query));
      return;
    }
    const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
    if (printable !== undefined) this.setQuery(this.query + printable);
  }

  /** Replace the query and re-anchor the cursor to the top of the results. */
  protected setQuery(query: string): void {
    this.query = query;
    this.index = 0;
  }
}
