import { visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it } from "vitest";

import { EditorDockComponent, NextTurnContextComponent } from "../screen.js";
import { ConversationStore } from "../store.js";

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
    expect(line).toContain("\x1b[48;");
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

    expect(lines).toHaveLength(3);
    expect(lines.every((line) => visibleWidth(line) === 32)).toBe(true);
    expect(lines.every((line) => line.includes("\x1b[48;"))).toBe(true);
  });
});

function stripAnsi(value: string): string {
  return value.replaceAll(new RegExp(`${String.fromCharCode(27)}\\[[\\d;]*m`, "g"), "");
}
