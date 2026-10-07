/** Fullscreen interactive session browser: list, search, rename, archive. */

import {
  decodeKittyPrintable,
  matchesKey,
  truncateToWidth,
  visibleWidth,
} from "@earendil-works/pi-tui";

import { type FleetApiClient, FleetApiError, type FleetSession } from "../../fleet-api-client.js";
import { relativeAge } from "../format.js";
import { MARKS } from "../marks.js";
import { dropLastGrapheme, terminalSafeLine } from "../terminal-text.js";
import { selectTheme, theme } from "../theme.js";

import { FilterableListOverlay } from "./list-overlay.js";
import { isPrintableInput, overlayHint, overlayRule, overlayTitle } from "./overlay.js";

const PAGE_SIZE = 10;
const TITLE_WIDTH = 44;

type SessionBrowserResult = { action: "resume"; id: string } | { action: "cancel" };

export class SessionBrowserOverlay extends FilterableListOverlay<FleetSession> {
  private sessions: FleetSession[];
  private readonly total: number;
  private renaming: string | null = null;
  private renameValue = "";
  private error: string | null = null;

  constructor(
    allSessions: FleetSession[],
    private readonly client: FleetApiClient,
    private readonly finish: (result: SessionBrowserResult) => void,
    private readonly requestRender: () => void,
    total?: number,
    private readonly onSessionUpdated: (session: FleetSession) => void = () => undefined,
  ) {
    super();
    this.sessions = [...allSessions];
    this.total = total ?? allSessions.length;
  }

  protected allItems(): readonly FleetSession[] {
    return this.sessions;
  }

  protected pageSize(): number {
    return PAGE_SIZE;
  }

  protected filterItems(items: readonly FleetSession[], query: string): FleetSession[] {
    const needle = query.trim().toLowerCase();
    if (!needle) return [...items];
    return items.filter(
      (session) =>
        session.title.toLowerCase().includes(needle) || session.id.toLowerCase().includes(needle),
    );
  }

  protected row(session: FleetSession): string {
    // Titles are caller-controlled backend strings: sanitize before display and
    // truncate by terminal cell width so a wide grapheme cannot split a column.
    const title = terminalSafeLine(session.title);
    const clipped = truncateToWidth(title, TITLE_WIDTH, "…");
    const padded = `${clipped}${" ".repeat(Math.max(0, TITLE_WIDTH - visibleWidth(clipped)))}`;
    const status =
      session.status === "archived"
        ? theme.fg("warning", "archived")
        : theme.fg("dim", session.status);
    return `${padded}  ${theme.fg("dim", relativeAge(session.updated_at))}  ${status}`;
  }

  protected footer(): ReadonlyArray<readonly [string, string]> {
    return [
      ["ENTER", "resume"],
      ["CTRL+R", "rename"],
      ["CTRL+A", "archive/unarchive"],
      ["ESC", "close"],
    ];
  }

  protected filterPlaceholder(): string {
    return "(type to search)";
  }

  protected emptyLabel(): string {
    return "No sessions found.";
  }

  protected confirm(session: FleetSession): void {
    this.finish({ action: "resume", id: session.id });
  }

  protected cancel(): void {
    this.finish({ action: "cancel" });
  }

  protected handleKey(data: string, session: FleetSession | undefined): boolean {
    // Actions use a modifier so bare letters stay available for filtering,
    // which would otherwise be impossible for titles containing "r" or "a".
    if (matchesKey(data, "ctrl+r")) {
      if (!session) return false;
      this.renaming = session.id;
      this.renameValue = terminalSafeLine(session.title);
      this.error = null;
      this.requestRender();
      return true;
    }
    if (matchesKey(data, "ctrl+a")) {
      if (!session) return false;
      void this.toggleArchive(session);
      return true;
    }
    return false;
  }

  /** The session count leads the shared page status. */
  protected renderStatus(): string {
    const count = this.filterItems(this.sessions, this.query).length;
    const truncated =
      this.total > this.sessions.length
        ? ` · showing ${this.sessions.length} of ${this.total}`
        : "";
    return `${count} session${count === 1 ? "" : "s"}${truncated} · ${super.renderStatus()}`;
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const lines = [
      overlayTitle("Sessions"),
      overlayHint("Switch, rename, or archive a Session"),
      overlayRule(safeWidth),
      this.renderFilter(),
      "",
    ];

    if (this.renaming !== null) {
      // The rename value is seeded from a backend session title and typed by the
      // operator: sanitize and clamp before it reaches the terminal.
      const rename = truncateToWidth(terminalSafeLine(this.renameValue), safeWidth - 10, "…");
      lines.push(overlayTitle(`Rename: ${rename || theme.fg("dim", "(type new title)")}`));
      if (this.error) lines.push(theme.fg("error", `${MARKS.error} ${this.error}`));
      lines.push("");
      lines.push(theme.fg("dim", "Enter confirm · Esc cancel"));
      return this.clip(lines, safeWidth);
    }

    lines.push(...this.renderRows());
    lines.push("");
    lines.push(selectTheme.scrollInfo(this.renderStatus()));
    lines.push(overlayRule(safeWidth));
    lines.push(this.renderFooter());
    if (this.error) lines.push(theme.fg("error", `${MARKS.error} ${this.error}`));
    return this.clip(lines, safeWidth);
  }

  override handleInput(data: string): void {
    if (this.renaming !== null) {
      this.handleRenameInput(data);
      return;
    }
    super.handleInput(data);
  }

  private handleRenameInput(data: string): void {
    const renamingId = this.renaming;
    if (!renamingId) return;
    if (matchesKey(data, "escape")) {
      this.renaming = null;
      this.renameValue = "";
      this.error = null;
      this.requestRender();
    } else if (matchesKey(data, "enter")) {
      const title = this.renameValue.trim();
      if (!title) {
        this.error = "Title cannot be empty.";
        this.requestRender();
        return;
      }
      void this.commitRename(renamingId, title);
    } else if (matchesKey(data, "backspace")) {
      this.renameValue = dropLastGrapheme(this.renameValue);
      this.requestRender();
    } else {
      const printable = decodeKittyPrintable(data) ?? (isPrintableInput(data) ? data : undefined);
      if (printable !== undefined) {
        this.renameValue += printable;
        this.requestRender();
      }
    }
  }

  private async commitRename(id: string, title: string): Promise<void> {
    try {
      const updated = await this.client.updateSession(id, { title });
      const index = this.sessions.findIndex((session) => session.id === id);
      if (index >= 0) {
        this.sessions = this.sessions.slice();
        this.sessions[index] = updated;
      }
      this.onSessionUpdated(updated);
      this.renaming = null;
      this.renameValue = "";
      this.error = null;
    } catch {
      this.error = "Rename failed. Try again.";
    }
    this.requestRender();
  }

  private async toggleArchive(session: FleetSession): Promise<void> {
    const next = session.status === "archived" ? "active" : "archived";
    try {
      const updated = await this.client.updateSession(session.id, { status: next });
      this.publishSessionUpdate(updated);
      this.error = null;
    } catch (error) {
      if (next === "archived" && isRetirementPending(error)) {
        // The API commits the archived status before trying to retire the
        // provider session, so a retirement-pending response still changes the
        // durable Session state.
        this.publishSessionUpdate({ ...session, status: "archived" });
        this.error = "Session archived, but provider retirement is pending.";
      } else {
        this.error = `Failed to ${next === "archived" ? "archive" : "unarchive"} session.`;
      }
    }
    this.requestRender();
  }

  private publishSessionUpdate(updated: FleetSession): void {
    const index = this.sessions.findIndex((item) => item.id === updated.id);
    if (index >= 0) {
      this.sessions = this.sessions.slice();
      this.sessions[index] = updated;
    }
    this.onSessionUpdated(updated);
  }
}

function isRetirementPending(error: unknown): boolean {
  return error instanceof FleetApiError && error.code === "session_retirement_pending";
}
