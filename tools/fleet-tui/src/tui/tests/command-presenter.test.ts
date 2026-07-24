import { visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it, vi } from "vitest";

import type { FleetSkillCard } from "../../fleet-api-client.js";
import { SkillSelector } from "../command-presenter.js";

const skills = Array.from({ length: 14 }, (_, index) => ({
  id: `skill-${index}`,
  name: index === 13 ? "long-context" : `skill-${index}`,
  description: `A deliberately long description for skill ${index} that must stay inside the overlay width.`,
  scope: "system",
  version: "1.0.0",
  trust: "system",
  affordances: [],
  resources_available: true,
})) satisfies FleetSkillCard[];

describe("SkillSelector", () => {
  it("keeps a bounded width-aware viewport and filters by typed input", () => {
    const selector = new SkillSelector(skills, [], vi.fn());

    const initial = selector.render(48);
    expect(initial.length).toBeLessThanOrEqual(15);
    expect(initial.every((line) => visibleWidth(line) <= 48)).toBe(true);
    expect(initial.join("\n")).toContain("\x1b[");

    for (const key of "context") selector.handleInput(key);
    const filtered = selector.render(48).join("\n");
    expect(filtered).toContain("long-context");
    expect(filtered).not.toContain("skill-0@");
  });

  it("removes a complete Unicode grapheme when filtering", () => {
    const selector = new SkillSelector(skills, [], vi.fn());

    selector.handleInput("x");
    selector.handleInput("e\u0301");
    selector.handleInput("\u007f");

    expect(stripAnsi(selector.render(48)[1] ?? "")).toBe("Filter: x");
  });

  it("keeps CJK, emoji, and combining marks within a narrow overlay", () => {
    const selector = new SkillSelector(
      [
        {
          ...skills[0]!,
          name: "調査😀e\u0301",
          description: "界面を確認する✅",
        },
      ],
      [],
      vi.fn(),
    );

    expect(selector.render(20).every((line) => visibleWidth(line) <= 20)).toBe(true);
  });
});

function stripAnsi(value: string): string {
  return value.replaceAll(new RegExp(`${String.fromCharCode(27)}\\[[\\d;]*m`, "g"), "");
}
