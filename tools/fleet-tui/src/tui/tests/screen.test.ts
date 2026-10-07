import { visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it } from "vitest";

import type { ExecutionSummary } from "../execution-summary.js";
import {
  EditorDockComponent,
  footerHints,
  footerMetrics,
  formatFooterZones,
  NextTurnContextComponent,
} from "../screen.js";
import { ConversationStore } from "../store.js";
import { hasBackground, stripAnsi } from "./support/ansi.js";

describe("NextTurnContextComponent", () => {
  it("stays hidden until the operator pins next-Turn inputs", () => {
    const store = new ConversationStore();

    expect(new NextTurnContextComponent(store).render(80)).toEqual([]);
  });

  it("keeps Skill and Attachment selections visible beside the editor", () => {
    const store = new ConversationStore();
    store.dispatch({
      type: "skill-selection/pin",
      selection: {
        id: "skill-1",
        expectedVersion: "1.2.0",
        displayName: "data-analysis",
      },
    });
    store.dispatch({
      type: "attachment/pin",
      attachment: { id: "attachment-1", filename: "brief.md", bytes: 2048 },
    });

    const rendered = stripAnsi(new NextTurnContextComponent(store).render(80).join("\n"));

    expect(rendered).toContain("NEXT TURN  1 Skill · 1 Attachment · 2.0KB");
    expect(rendered).toContain("data-analysis@1.2.0");
    expect(rendered).toContain("brief.md");
  });

  it("sanitizes labels and stays within narrow terminal widths", () => {
    const store = new ConversationStore();
    store.dispatch({
      type: "skill-selection/pin",
      selection: {
        id: "skill-1",
        expectedVersion: "1.0.0",
        displayName: "unsafe\nname\u001b]52;c;secret\u0007",
      },
    });
    store.dispatch({
      type: "attachment/pin",
      attachment: { id: "attachment-1", filename: "notes\nprivate.txt", bytes: 3 },
    });

    const component = new NextTurnContextComponent(store);
    const line = component.render(42)[0] ?? "";
    const plain = stripAnsi(component.render(160).join("\n"));

    expect(visibleWidth(line)).toBe(42);
    expect(hasBackground(line)).toBe(true);
    expect(stripAnsi(line)).toContain("1 Attachment");
    expect(plain).not.toContain("secret");
    expect(plain).not.toContain("\n");
    expect(plain).toContain("NEXT TURN");
  });
});

describe("EditorDockComponent", () => {
  it("groups the pi-tui editor on one adaptive full-width surface", () => {
    const editor = {
      invalidate() {},
      render(width: number) {
        return ["─".repeat(width), " prompt", "─".repeat(width)];
      },
    };

    const lines = new EditorDockComponent(editor).render(32);

    // A labeled cue line precedes the editor's own three surfaces.
    expect(lines).toHaveLength(4);
    expect(lines.every((line) => visibleWidth(line) === 32)).toBe(true);
    expect(lines.every((line) => hasBackground(line))).toBe(true);
    expect(stripAnsi(lines[0] ?? "")).toContain("Ask Fleet");
  });
});

describe("footer layout", () => {
  const summary: ExecutionSummary = {
    iterations: 3,
    subLmCalls: 0,
    hostCapabilityCalls: 0,
    interpreterErrors: 2,
    durationMs: 35_000,
  };
  const leftZone = `TOKENS  ↑ 19k  ↓ 2.5k${footerMetrics(summary)}`;

  it("omits zero-valued optional metric cells", () => {
    expect(footerMetrics(summary)).toBe("  ·  3 iter · 2 errors · 0:35");
  });

  it("does not render a misleading partial token at 80 columns", () => {
    const line = stripAnsi(formatFooterZones(leftZone, footerHints("idle"), 80)[0] ?? "");

    // Regression: a fixed 40-col hint reservation cut "0 sub-LM" down to "0 s".
    expect(line).not.toMatch(/\b0 s\b/);
    expect(line).toContain("3 iter · 2 errors · 0:35");
    expect(line).toContain("Enter send · / commands");
  });

  it("marks a truncated metrics zone with an ellipsis and preserves width", () => {
    const long = `TOKENS  ↑ 19k  ↓ 2.5k  ·  3 iter · 8 sub-LM · 12 host · 9 errors · 12:34`;
    const line = formatFooterZones(long, footerHints("idle"), 80)[0] ?? "";

    expect(stripAnsi(line)).toContain("…");
    expect(visibleWidth(line)).toBe(80);
  });

  it("keeps the running hint short so metrics still fit", () => {
    const line = stripAnsi(formatFooterZones(leftZone, footerHints("running"), 80)[0] ?? "");

    expect(line).toContain("Esc cancel");
    expect(line).toContain("3 iter · 2 errors · 0:35");
  });

  it("never exceeds the viewport at degenerate widths", () => {
    for (const width of [1, 2, 3]) {
      const line = formatFooterZones(leftZone, footerHints("idle"), width)[0] ?? "";
      expect(visibleWidth(line)).toBeLessThanOrEqual(width);
    }
  });
});
