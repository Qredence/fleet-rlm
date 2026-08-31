/** Shared overlay scaffold for Fleet TUI presenter surfaces. */

import {
  Box,
  type Component,
  decodeKittyPrintable,
  matchesKey,
  type SelectItem,
  SelectList,
  truncateToWidth,
} from "@earendil-works/pi-tui";

import { dropLastGrapheme } from "../terminal-text.js";
import { selectTheme, theme } from "../theme.js";

/** Shared geometry for presenter overlays: readable but inside small terminals. */
export const OVERLAY_OPTIONS = { width: "80%", maxHeight: "80%", anchor: "center" } as const;

/**
 * A quiet, keyboard-first modal surface. `Box` owns the adaptive fill and
 * padding while the child keeps ownership of selection and input behavior.
 */
export class ModalSurface extends Box {
  constructor(private readonly inner: Component) {
    super(2, 1, (text) => theme.surface("customMessageBg")(text));
    this.addChild(inner);
  }

  handleInput(data: string): void {
    this.inner.handleInput?.(data);
  }
}

/** Shared Fleet-styled overlay header line. */
export function overlayTitle(text: string): string {
  return `${theme.fg("dim", "MENU")}  ${theme.fg("accent", theme.bold(text))}`;
}

/** Applies Fleet's dim styling to overlay hint text.

 * @param text - The hint text to style
 * @returns The dim-styled hint text
 */
export function overlayHint(text: string): string {
  return theme.fg("dim", text);
}

/** A light structural rule that separates modal metadata from active content. */
export function overlayRule(width: number): string {
  return theme.fg("borderMuted", "─".repeat(Math.max(1, width)));
}

/**
 * Wraps a `SelectList` with the shared Fleet overlay pattern: a title, an
 * optional context line, an optional bottom hint, optional filter-as-you-type,
 * and an optional preselected value. Input the list does not handle (printable
 * characters) feeds the filter; navigation/confirm/cancel stay with the list.
 */
export class SelectOverlay implements Component {
  onSelect?: (item: SelectItem) => void;
  onCancel?: () => void;
  private readonly list: SelectList;
  private query = "";

  constructor(
    items: SelectItem[],
    private readonly options: {
      title: string;
      context?: string;
      hint?: string;
      filterable?: boolean;
      selectedValue?: string;
      maxVisible?: number;
    },
  ) {
    // Leave room for the modal title, filter, and key footer inside pi-tui's
    // 80%-height overlay budget on a conventional 80×24 terminal.
    this.list = new SelectList(items, options.maxVisible ?? 8, selectTheme);
    this.list.onSelect = (item) => this.onSelect?.(item);
    this.list.onCancel = () => this.onCancel?.();
    if (options.selectedValue !== undefined) {
      const index = items.findIndex((item) => item.value === options.selectedValue);
      if (index > 0) this.list.setSelectedIndex(index);
    }
  }

  invalidate(): void {
    this.list.invalidate();
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const lines = [overlayTitle(this.options.title)];
    if (this.options.context) lines.push(overlayHint(this.options.context));
    lines.push(overlayRule(safeWidth));
    if (this.options.filterable) {
      lines.push(
        `${theme.fg("muted", "Filter:")} ${this.query || theme.fg("dim", "(type to filter)")}`,
      );
    }
    lines.push("");
    lines.push(...this.list.render(safeWidth));
    if (this.options.hint) {
      lines.push("");
      lines.push(overlayRule(safeWidth));
      lines.push(`${theme.fg("accent", "ESC")} ${overlayHint(`close  ·  ${this.options.hint}`)}`);
    }
    return lines.map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    if (this.options.filterable) {
      if (matchesKey(data, "backspace")) {
        this.query = dropLastGrapheme(this.query);
        this.list.setFilter(this.query);
        return;
      }
      const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
      if (printable) {
        this.query += printable;
        this.list.setFilter(this.query);
        return;
      }
    }
    this.list.handleInput(data);
  }

  /** Exposed for tests: the list's current filter query. */
  get filterQuery(): string {
    return this.query;
  }
}

/**
 * Prepends the shared Fleet title/context header to any component, keeping
 * the wrapped component's own bottom hint (SettingsList renders its own).
 */
export class TitledComponent implements Component {
  constructor(
    private readonly inner: Component,
    private readonly titleText: string,
    private readonly contextText?: string,
  ) {}

  invalidate(): void {
    this.inner.invalidate();
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const lines = [overlayTitle(this.titleText)];
    if (this.contextText) lines.push(overlayHint(this.contextText));
    lines.push(overlayRule(safeWidth));
    lines.push("");
    lines.push(...this.inner.render(safeWidth));
    return lines.map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    this.inner.handleInput?.(data);
  }
}

/**
 * Determines whether input consists entirely of printable Unicode characters.
 *
 * @param value - The input string to evaluate
 * @returns `true` if the string is nonempty and contains only printable characters, `false` otherwise.
 */
export function isPrintableInput(value: string): boolean {
  return (
    value.length > 0 &&
    Array.from(value).every((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint >= 0x20 && codePoint !== 0x7f;
    })
  );
}
