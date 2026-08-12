import { visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it, vi } from "vitest";

import type { FleetSettingsPolicy, FleetSkillCard } from "../../fleet-api-client.js";
import { MultiChoiceEditor, SkillSelector, TextSettingEditor } from "../command-presenter.js";

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

const settings = {
  revision: "a".repeat(64),
  active_profile: "daytona",
  restart_required: true,
  scopes: [
    {
      name: "defaults",
      fields: [
        {
          path: "rlm.verbose",
          group: "RLM",
          label: "DSPy host verbose logging",
          value: true,
          editor: "boolean",
          choices: [],
          environment_overridden: false,
        },
      ],
    },
  ],
} satisfies FleetSettingsPolicy;

describe("TextSettingEditor", () => {
  it("edits text values (pre-filled with the current value) and confirms with Enter", () => {
    const field = settings.scopes[0]!.fields[0]!;
    const finish = vi.fn();
    const editor = new TextSettingEditor({ ...field, value: "old", editor: "text" }, finish);

    editor.handleInput("new");
    expect(stripAnsi(editor.render(40).join("\n"))).toContain("oldnew");
    editor.handleInput("\r");
    expect(finish).toHaveBeenCalledWith("oldnew");
  });

  it("cancels with Escape and refuses empty values", () => {
    const field = settings.scopes[0]!.fields[0]!;
    const finish = vi.fn();
    const editor = new TextSettingEditor({ ...field, value: "", editor: "text" }, finish);

    editor.handleInput("\x1b");
    expect(finish).toHaveBeenCalledWith(undefined);

    const second = new TextSettingEditor({ ...field, value: "", editor: "text" }, finish);
    second.handleInput("\r");
    expect(finish).toHaveBeenCalledTimes(1);
  });
});

describe("MultiChoiceEditor", () => {
  it("toggles choices with Space and confirms the joined selection", () => {
    const field = {
      ...settings.scopes[0]!.fields[0]!,
      editor: "multi_choice" as const,
      value: ["a"],
      choices: ["a", "b", "c"],
    };
    const finish = vi.fn();
    const editor = new MultiChoiceEditor(field, finish);

    editor.handleInput(" ");
    expect(stripAnsi(editor.render(40).join("\n"))).toContain("[ ] a");

    editor.handleInput("\x1b[B");
    editor.handleInput(" ");
    expect(stripAnsi(editor.render(40).join("\n"))).toContain("[x] b");

    editor.handleInput("\r");
    expect(finish).toHaveBeenCalledWith("b");
  });
});

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
