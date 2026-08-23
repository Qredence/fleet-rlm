/** Shared in-memory `Terminal` test double for TUI tests. */

import type { Terminal } from "@earendil-works/pi-tui";

export class FakeTerminal implements Terminal {
  columns = 80;
  rows = 24;
  kittyProtocolActive = false;
  writes: string[] = [];
  progress: boolean[] = [];
  private onInput?: (data: string) => void;
  private onResize?: () => void;

  start(onInput: (data: string) => void, onResize: () => void): void {
    this.onInput = onInput;
    this.onResize = onResize;
  }
  stop(): void {}
  async drainInput(): Promise<void> {}
  write(data: string): void {
    this.writes.push(data);
  }
  moveBy(): void {}
  hideCursor(): void {}
  showCursor(): void {}
  clearLine(): void {}
  clearFromCursor(): void {}
  clearScreen(): void {}
  setTitle(): void {}
  setProgress(active: boolean): void {
    this.progress.push(active);
  }
  send(data: string): void {
    this.onInput?.(data);
  }
  resize(columns: number, rows: number): void {
    this.columns = columns;
    this.rows = rows;
    this.onResize?.();
  }
}
