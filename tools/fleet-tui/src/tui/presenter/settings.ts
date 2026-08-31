/** Settings-list row builders and field editors for the Fleet settings overlay. */

import {
  type Component,
  decodeKittyPrintable,
  matchesKey,
  type SettingItem,
  truncateToWidth,
} from "@earendil-works/pi-tui";

import type { FleetSettingsPolicy } from "../../fleet-api-client.js";
import type { SettingsUpdate } from "../commands/registry.js";
import { dropLastGrapheme } from "../terminal-text.js";
import { selectTheme, theme } from "../theme.js";

import { isPrintableInput, overlayHint, overlayRule, overlayTitle } from "./overlay.js";

export type SettingsField = FleetSettingsPolicy["scopes"][number]["fields"][number];

/**
 * Creates a selectable settings item with an editor appropriate for the field type.
 * Fields pinned by the environment, and singleton `single_choice` values (such
 * as `runtime.environment=daytona`), render as fixed/read-only rows: cycling
 * them would PATCH a meaningless no-op.
 *
 * @param field - The settings field to represent
 * @returns A settings item containing the field's display information and editing options
 */
export function fieldItem(field: SettingsField): SettingItem {
  const base = {
    id: field.path,
    label: `${field.group} · ${field.label}`,
    description: describeField(field),
    currentValue: displayValue(field.value),
  };
  if (field.environment_overridden || isFixedChoice(field)) return base;
  if (field.editor === "boolean" || field.editor === "single_choice") {
    let values: string[];
    if (field.editor === "boolean") {
      values = ["false", "true"];
    } else {
      const choices = choicesOf(field);
      values = choices.length > 0 ? choices : [displayValue(field.value)];
    }
    return {
      ...base,
      values,
    };
  }
  if (field.editor === "multi_choice") {
    return {
      ...base,
      submenu: (_current, done) => new MultiChoiceEditor(field, done),
    };
  }
  if (field.editor === "string_list") {
    const display = Array.isArray(field.value)
      ? field.value.filter((item): item is string => typeof item === "string").join(", ")
      : String(field.value ?? "");
    return {
      ...base,
      submenu: (_current, done) =>
        new TextSettingEditor({ ...field, value: display }, done, { allowEmpty: true }),
    };
  }
  return {
    ...base,
    submenu: (_current, done) =>
      new TextSettingEditor(field, done, { numeric: field.editor === "number" }),
  };
}

/**
 * Determines whether a single-choice field has at most one available choice.
 *
 * @returns `true` if the field uses a single-choice editor and has at most one choice, `false` otherwise.
 */
function isFixedChoice(field: SettingsField): boolean {
  return field.editor === "single_choice" && (field.choices?.length ?? 0) <= 1;
}

export type ParsedFieldValue =
  | { ok: true; value: string | number | boolean | string[] }
  | { ok: false; error: string };

/**
 * Converts editor text into the typed value for a settings update.
 *
 * @param field - The settings field being edited
 * @param raw - The raw editor input
 * @returns A parsed value, or an error message when numeric input is blank or invalid
 */
export function parseFieldValue(field: SettingsField, raw: string): ParsedFieldValue {
  if (field.editor === "boolean") return { ok: true, value: raw === "true" };
  if (field.editor === "number") {
    const trimmed = raw.trim();
    const parsed = Number(trimmed);
    if (!trimmed || !Number.isFinite(parsed)) {
      return { ok: false, error: `“${trimmed}” is not a number` };
    }
    return { ok: true, value: parsed };
  }
  if (field.editor === "multi_choice" || field.editor === "string_list") {
    return {
      ok: true,
      value: raw
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    };
  }
  return { ok: true, value: raw };
}

/**
 * Creates a settings update from raw field input and completes the editing operation.
 *
 * @param settings - Current settings policy used to assign the update revision
 * @param scope - Settings scope for the update
 * @param field - Field being updated
 * @param raw - Raw value entered for the field
 * @param finish - Callback receiving the update, or `null` when the value is invalid
 */
export function applyFieldValue(
  settings: FleetSettingsPolicy,
  scope: string,
  field: SettingsField,
  raw: string,
  finish: (update: SettingsUpdate | null) => void,
): void {
  const parsed = parseFieldValue(field, raw);
  if (!parsed.ok) {
    finish(null);
    return;
  }
  finish({
    revision: settings.revision,
    scope,
    path: field.path,
    value: parsed.value,
  });
}

/** Minimal keyboard text editor for text/number settings. */
export class TextSettingEditor implements Component {
  private value: string;
  private readonly allowEmpty: boolean;
  private readonly numeric: boolean;
  private error: string | null = null;

  constructor(
    private readonly field: SettingsField,
    private readonly finish: (value?: string) => void,
    options: { allowEmpty?: boolean; numeric?: boolean } = {},
  ) {
    // Never pre-fill a literal "undefined"/"null" — unset values start empty.
    this.value = field.value === undefined || field.value === null ? "" : String(field.value);
    this.allowEmpty = options.allowEmpty === true;
    this.numeric = options.numeric === true;
  }

  invalidate(): void {}

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const lines = [
      overlayTitle(`${this.field.group} · ${this.field.label}`),
      overlayHint(`Current: ${displayValue(this.field.value)}${this.numeric ? " (number)" : ""}`),
      overlayRule(safeWidth),
      "",
      `${theme.fg("muted", "New value:")} ${this.value || theme.fg("dim", "(type a value)")}`,
    ];
    if (this.error) {
      lines.push(theme.fg("error", this.error));
    }
    lines.push(
      "",
      overlayRule(safeWidth),
      `${theme.fg("accent", "ENTER")} ${overlayHint("save  ·  Esc back")}`,
    );
    return lines.map((line) => truncateToWidth(line, safeWidth, "…"));
  }

  handleInput(data: string): void {
    if (matchesKey(data, "escape")) {
      this.finish(undefined);
    } else if (matchesKey(data, "enter")) {
      const trimmed = this.value.trim();
      if (!this.allowEmpty && !trimmed) return;
      // Validate numbers here so an invalid edit keeps the editor open with
      // a visible error instead of saving NaN (JSON `null`) into the policy.
      if (this.numeric && (trimmed === "" || !Number.isFinite(Number(trimmed)))) {
        this.error = "Enter a number, or press Escape to go back.";
        return;
      }
      this.error = null;
      this.finish(this.value);
    } else if (matchesKey(data, "backspace")) {
      this.value = dropLastGrapheme(this.value);
      this.error = null;
    } else {
      const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
      if (printable) {
        this.value += printable;
        this.error = null;
      }
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
      overlayTitle(`${this.field.group} · ${this.field.label}`),
      overlayHint("Choose the values to write on the next save"),
      overlayRule(safeWidth),
      "",
      ...(choices.length > 0 ? choices : ["(no choices)"]).map((choice, index) => {
        const checked = this.selected.includes(choice) ? "x" : " ";
        const label = `[${checked}] ${choice}`;
        const selected = index === this.index;
        return `${selected ? selectTheme.selectedPrefix(">") : " "} ${selected ? selectTheme.selectedText(label) : label}`;
      }),
      "",
      overlayRule(safeWidth),
      `${theme.fg("accent", "SPACE")} ${overlayHint("toggle  ·  Enter confirm  ·  Esc back")}`,
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

/**
 * Gets the available choices for a settings field.
 *
 * @param field - The settings field to inspect
 * @returns The field's choices, or an empty array when none are defined
 */
function choicesOf(field: SettingsField): string[] {
  return field.choices ?? [];
}

/**
 * Composes the settings-row description: the path context plus read-only
 * markers for environment-pinned or fixed-value fields.
 *
 * @param field - The settings field to describe
 * @returns A contextual description for the field row
 */
function describeField(field: SettingsField): string {
  const parts = [settingDescription(field)];
  if (field.environment_overridden) {
    parts.push("set by environment variable; read-only");
  } else if (isFixedChoice(field)) {
    parts.push("fixed; only one value is supported");
  }
  return parts.join(" · ");
}

/**
 * Generates a contextual description for a settings field path.
 *
 * @param field - The settings field to describe
 * @returns A contextual description for recognized field paths, or the field path
 */
function settingDescription(field: SettingsField): string {
  if (field.path.endsWith(".api_key_env"))
    return `${field.path} · variable name only; value never shown`;
  if (field.path.endsWith(".base_url_env"))
    return `${field.path} · variable name only; value never shown`;
  if (field.path.endsWith(".base_url")) return `${field.path} · OpenAI-compatible /v1 base URL`;
  if (field.path.endsWith(".model")) return `${field.path} · provider model id`;
  return field.path;
}

/**
 * Formats a value for display.
 *
 * @param value - The value to format
 * @returns The value itself when it is a string; the JSON representation for
 *   defined values; "(unset)" when the value is undefined or null (never the
 *   literal "undefined"/"null" that `JSON.stringify` would leak)
 */
export function displayValue(value: unknown): string {
  if (value === undefined || value === null) return "(unset)";
  if (typeof value === "string") return value;
  return JSON.stringify(value) ?? "(unset)";
}
