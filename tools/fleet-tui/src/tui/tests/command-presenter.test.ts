import {
  type Component,
  type Editor,
  type OverlayHandle,
  type TUI,
  visibleWidth,
} from "@earendil-works/pi-tui";
import { describe, expect, it, vi } from "vitest";

import type { FleetSettingsPolicy, FleetSkillCard } from "../../fleet-api-client.js";
import {
  fieldItem,
  MultiChoiceEditor,
  ModalSurface,
  parseFieldValue,
  PiCommandPresenter,
  SelectOverlay,
  SkillSelector,
  TextSettingEditor,
} from "../command-presenter.js";
import type { SettingsSaveCallback } from "../commands.js";
import { ConversationStore } from "../store.js";

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

function defaultsField(): FleetSettingsPolicy["scopes"][number]["fields"][number] {
  const field = settings.scopes[0]?.fields[0];
  if (!field) {
    throw new Error("expected settings.scopes[0].fields[0]");
  }
  return field;
}

function firstSkill(): FleetSkillCard {
  const skill = skills[0];
  if (!skill) {
    throw new Error("expected skills[0]");
  }
  return skill;
}

describe("TextSettingEditor", () => {
  it("edits text values (pre-filled with the current value) and confirms with Enter", () => {
    const field = defaultsField();
    const finish = vi.fn();
    const editor = new TextSettingEditor({ ...field, value: "old", editor: "text" }, finish);

    editor.handleInput("new");
    expect(stripAnsi(editor.render(40).join("\n"))).toContain("oldnew");
    editor.handleInput("\r");
    expect(finish).toHaveBeenCalledWith("oldnew");
  });

  it("cancels with Escape and refuses empty values", () => {
    const field = defaultsField();
    const finish = vi.fn();
    const editor = new TextSettingEditor({ ...field, value: "", editor: "text" }, finish);

    editor.handleInput("\x1b");
    expect(finish).toHaveBeenCalledWith(undefined);

    const second = new TextSettingEditor({ ...field, value: "", editor: "text" }, finish);
    second.handleInput("\r");
    expect(finish).toHaveBeenCalledTimes(1);
  });

  it("allows empty Enter when allowEmpty is set", () => {
    const field = defaultsField();
    const finish = vi.fn();
    const editor = new TextSettingEditor({ ...field, value: "", editor: "text" }, finish, {
      allowEmpty: true,
    });

    editor.handleInput("\r");
    expect(finish).toHaveBeenCalledWith("");
  });

  it("removes a complete Unicode grapheme on backspace", () => {
    const field = defaultsField();
    const editor = new TextSettingEditor({ ...field, value: "", editor: "text" }, vi.fn());

    editor.handleInput("x");
    editor.handleInput("e\u0301");
    expect(valueLine(editor)).toBe("New value: xe\u0301");

    editor.handleInput("\u007f");
    expect(valueLine(editor)).toBe("New value: x");
  });

  it("removes a multi-codepoint emoji ZWJ sequence in one backspace", () => {
    const field = defaultsField();
    const editor = new TextSettingEditor({ ...field, value: "ab", editor: "text" }, vi.fn());

    editor.handleInput("\ud83d\udc68\u200d\ud83d\udc69\u200d\ud83d\udc67\u200d\ud83d\udc66");
    expect(valueLine(editor)).toBe(
      "New value: ab\ud83d\udc68\u200d\ud83d\udc69\u200d\ud83d\udc67\u200d\ud83d\udc66",
    );

    editor.handleInput("\u007f");
    expect(valueLine(editor)).toBe("New value: ab");
  });

  it("renders an unset current value without a literal undefined", () => {
    const field = defaultsField();
    const editor = new TextSettingEditor({ ...field, value: undefined, editor: "text" }, vi.fn());

    expect(stripAnsi(editor.render(40).join("\n"))).toContain("Current: (unset)");
    expect(stripAnsi(editor.render(40).join("\n"))).not.toContain("undefined");
  });
});

function valueLine(editor: TextSettingEditor): string {
  return (
    stripAnsi(editor.render(40).join("\n"))
      .split("\n")
      .find((line) => line.startsWith("New value:")) ?? ""
  );
}

describe("MultiChoiceEditor", () => {
  it("toggles choices with Space and confirms the joined selection", () => {
    const field = {
      ...defaultsField(),
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
    // The shared modal adds title/context/rule/footer chrome while staying
    // within the 80%-of-24-row overlay budget.
    expect(initial.length).toBeLessThanOrEqual(17);
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

    expect(stripAnsi(selector.render(48).find((line) => line.includes("Filter:")) ?? "")).toBe(
      "Filter: x",
    );
  });

  it("keeps CJK, emoji, and combining marks within a narrow overlay", () => {
    const selector = new SkillSelector(
      [
        {
          ...firstSkill(),
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

describe("TextSettingEditor numeric fields", () => {
  it("rejects non-numeric input with an inline error and stays open", () => {
    const field = defaultsField();
    const finish = vi.fn();
    const editor = new TextSettingEditor({ ...field, value: "4", editor: "number" }, finish, {
      numeric: true,
    });

    editor.handleInput("x");
    editor.handleInput("\r");
    expect(finish).not.toHaveBeenCalled();
    expect(stripAnsi(editor.render(60).join("\n"))).toContain("Enter a number");

    editor.handleInput("\u007f");
    editor.handleInput("\r");
    expect(finish).toHaveBeenCalledWith("4");
  });
});

describe("fieldItem", () => {
  function makeField(
    overrides: Partial<ReturnType<typeof defaultsField>> = {},
  ): ReturnType<typeof defaultsField> {
    return { ...defaultsField(), ...overrides };
  }

  it("marks environment-overridden fields read-only with no editor affordance", () => {
    const item = fieldItem(
      makeField({ editor: "text", value: "gemini", environment_overridden: true }),
    );
    expect(item.values).toBeUndefined();
    expect(item.submenu).toBeUndefined();
    expect(item.description).toContain("read-only");
    expect(item.currentValue).toBe("gemini");
  });

  it("marks singleton single_choice fields fixed/read-only to avoid no-op PATCHes", () => {
    const item = fieldItem(
      makeField({
        path: "runtime.environment",
        editor: "single_choice",
        value: "daytona",
        choices: ["daytona"],
      }),
    );
    expect(item.values).toBeUndefined();
    expect(item.submenu).toBeUndefined();
    expect(item.description).toContain("fixed");
    expect(item.currentValue).toBe("daytona");
  });

  it("still offers a cycle editor for multi-value single_choice fields", () => {
    const item = fieldItem(makeField({ editor: "single_choice", value: "a", choices: ["a", "b"] }));
    expect(item.values).toEqual(["a", "b"]);
    expect(item.description).not.toContain("read-only");
  });

  it("says api_key_env paths are variable names whose value is never shown", () => {
    const item = fieldItem(makeField({ path: "llm.api_key_env", value: "GEMINI_API_KEY" }));
    expect(item.description).toContain("variable name only; value never shown");
    expect(item.currentValue).toBe("GEMINI_API_KEY");
  });

  it("renders undefined and null values as (unset)", () => {
    expect(fieldItem(makeField({ value: undefined })).currentValue).toBe("(unset)");
    expect(fieldItem(makeField({ value: null })).currentValue).toBe("(unset)");
  });
});

describe("parseFieldValue", () => {
  const base = defaultsField();

  it("rejects NaN numbers instead of PATCHing JSON null", () => {
    const result = parseFieldValue({ ...base, editor: "number" }, "abc");
    expect(result).toEqual({ ok: false, error: expect.stringContaining("not a number") });
    expect(parseFieldValue({ ...base, editor: "number" }, "")).toMatchObject({ ok: false });
  });

  it("parses numbers, booleans, and comma lists", () => {
    expect(parseFieldValue({ ...base, editor: "number" }, "42")).toEqual({ ok: true, value: 42 });
    expect(parseFieldValue({ ...base, editor: "boolean" }, "true")).toEqual({
      ok: true,
      value: true,
    });
    expect(parseFieldValue({ ...base, editor: "string_list" }, "a, b ,,c")).toEqual({
      ok: true,
      value: ["a", "b", "c"],
    });
  });
});

describe("SelectOverlay", () => {
  const items = [
    { value: "dark", label: "dark (current)", description: "active theme" },
    { value: "light", label: "light", description: "select to apply" },
    { value: "midnight", label: "midnight", description: "custom theme" },
  ];

  function rendered(overlay: SelectOverlay, width = 60): string {
    return stripAnsi(overlay.render(width).join("\n"));
  }

  it("renders the shared title, context, and bottom hint", () => {
    const overlay = new SelectOverlay(items, {
      title: "Select theme",
      context: "Current: dark",
      hint: "Type to filter · Enter apply · Esc cancel",
    });
    const output = rendered(overlay);
    expect(output).toContain("Select theme");
    expect(output).toContain("MENU");
    expect(output).toContain("Current: dark");
    expect(output).toContain("Enter apply · Esc cancel");
    expect(overlay.render(60).every((line) => visibleWidth(line) <= 60)).toBe(true);
  });

  it("uses a padded adaptive pi-tui Box surface without swallowing picker input", () => {
    const overlay = new SelectOverlay(items, {
      title: "Select theme",
      hint: "Enter apply",
    });
    const surface = new ModalSurface(overlay);
    const onSelect = vi.fn();
    overlay.onSelect = onSelect;

    const lines = surface.render(44);
    expect(lines.every((line) => visibleWidth(line) === 44)).toBe(true);
    expect(lines.every((line) => line.includes("\x1b[48;"))).toBe(true);
    expect(stripAnsi(lines.join("\n"))).toContain("ESC close");

    surface.handleInput("\r");
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ value: "dark" }));
  });

  it("preselects the configured value", () => {
    const overlay = new SelectOverlay(items, { title: "t", selectedValue: "midnight" });
    expect(rendered(overlay)).toContain("→ midnight");
  });

  it("filters as you type and selects the narrowed item", () => {
    const overlay = new SelectOverlay(items, { title: "t", filterable: true });
    const onSelect = vi.fn();
    overlay.onSelect = onSelect;

    for (const key of "mid") overlay.handleInput(key);
    expect(overlay.filterQuery).toBe("mid");
    const filtered = rendered(overlay);
    expect(filtered).toContain("midnight");
    expect(filtered).not.toContain("dark");

    overlay.handleInput("\r");
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ value: "midnight" }));
  });

  it("removes a complete grapheme from the filter on backspace", () => {
    const overlay = new SelectOverlay(items, { title: "t", filterable: true });
    overlay.handleInput("l");
    overlay.handleInput("e\u0301");
    expect(overlay.filterQuery).toBe("le\u0301");
    overlay.handleInput("\u007f");
    expect(overlay.filterQuery).toBe("l");
  });

  it("cancels through the wrapped list", () => {
    const overlay = new SelectOverlay(items, { title: "t" });
    const onCancel = vi.fn();
    overlay.onCancel = onCancel;
    overlay.handleInput("\x1b");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe("PiCommandPresenter", () => {
  const ENTER = "\r";
  const ESCAPE = "\x1b";
  const DOWN = "\x1b[B";

  function editableSettings(): FleetSettingsPolicy {
    return {
      revision: "a".repeat(64),
      active_profile: "daytona",
      default_profile: "daytona",
      restart_required: true,
      scopes: [
        {
          name: "daytona",
          fields: [
            {
              path: "rlm.max_iters",
              group: "RLM",
              label: "Max iterations",
              value: 4,
              editor: "number" as const,
              choices: [],
              environment_overridden: false,
            },
            {
              path: "rlm.verbose",
              group: "RLM",
              label: "Verbose logging",
              value: false,
              editor: "boolean" as const,
              choices: [],
              environment_overridden: false,
            },
          ],
        },
      ],
    };
  }

  type InteractiveComponent = Component & { handleInput: (data: string) => void };

  function fakeUi(): {
    ui: TUI;
    overlay: () => InteractiveComponent;
    hide: ReturnType<typeof vi.fn>;
  } {
    let component: InteractiveComponent | null = null;
    const hide = vi.fn();
    const ui = {
      showOverlay: (c: Component) => {
        component = c as InteractiveComponent;
        return { hide } as unknown as OverlayHandle;
      },
      setFocus: vi.fn(),
    } as unknown as TUI;
    return {
      ui,
      overlay: () => {
        if (!component) throw new Error("no overlay shown");
        return component;
      },
      hide,
    };
  }

  it("shows a filterable help palette and inserts the selected command", () => {
    const { ui, overlay, hide } = fakeUi();
    const setText = vi.fn();
    const presenter = new PiCommandPresenter(
      ui,
      { setText } as unknown as Editor,
      new ConversationStore(),
    );

    presenter.showHelp([
      { name: "help", usage: "/help", description: "Show help", handler: vi.fn() },
      { name: "status", usage: "/status", description: "Show status", handler: vi.fn() },
    ]);
    const screen = overlay();
    expect(stripAnsi(screen.render(80).join("\n"))).toContain("2 commands");
    expect(stripAnsi(screen.render(80).join("\n"))).toContain("Ctrl+Shift+F search");
    expect(stripAnsi(screen.render(80).join("\n"))).not.toContain("PgUp/PgDn scroll");

    for (const key of "status") screen.handleInput(key);
    const filtered = stripAnsi(screen.render(80).join("\n"));
    expect(filtered).toContain("Filter: status");
    expect(filtered).toContain("/status");
    expect(filtered).not.toContain("/help");

    screen.handleInput(ENTER);
    expect(setText).toHaveBeenCalledWith("/status ");
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it("keeps the overlay open across successive saves and uses the freshest revision", async () => {
    const { ui, overlay, hide } = fakeUi();
    const notify = vi.fn();
    const presenter = new PiCommandPresenter(
      ui,
      { setText: vi.fn() } as unknown as Editor,
      new ConversationStore(),
      notify,
    );
    const settings = editableSettings();
    const refreshed = { ...settings, revision: "b".repeat(64) };
    const save = vi.fn<SettingsSaveCallback>().mockResolvedValue(refreshed);

    const result = presenter.chooseSetting({ ...settings }, save);
    const screen = overlay();
    const text = () => stripAnsi(screen.render(80).join("\n"));
    expect(text()).toContain("Fleet settings");

    // Open the scope, edit the number field: "4" -> "8"
    screen.handleInput(ENTER);
    screen.handleInput(ENTER);
    screen.handleInput("\u007f");
    screen.handleInput("8");
    screen.handleInput(ENTER);
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]?.[0]).toEqual({
      revision: "a".repeat(64),
      scope: "daytona",
      path: "rlm.max_iters",
      value: 8,
    });

    // The overlay stayed open and shows the saved value from the refreshed policy
    expect(hide).not.toHaveBeenCalled();
    expect(text()).toContain("rlm.max_iters");

    // Cycle the boolean field; the save must use the refreshed revision
    screen.handleInput(DOWN);
    screen.handleInput(ENTER);
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1]?.[0]).toEqual({
      revision: "b".repeat(64),
      scope: "daytona",
      path: "rlm.verbose",
      value: true,
    });

    // Escape twice: field list -> scope list -> close, which resolves the command
    screen.handleInput(ESCAPE);
    screen.handleInput(ESCAPE);
    await expect(result).resolves.toBeNull();
    expect(hide).toHaveBeenCalled();
  });

  it("flashes a parse error and does not save when a number field gets NaN input", async () => {
    const { ui, overlay } = fakeUi();
    const notify = vi.fn();
    const presenter = new PiCommandPresenter(
      ui,
      { setText: vi.fn() } as unknown as Editor,
      new ConversationStore(),
      notify,
    );
    const save = vi.fn<SettingsSaveCallback>();
    const result = presenter.chooseSetting(editableSettings(), save);
    const screen = overlay();

    screen.handleInput(ENTER);
    screen.handleInput(ENTER);
    screen.handleInput("x");
    screen.handleInput(ENTER);
    // Editor-level validation keeps the editor open; no save, visible error
    expect(stripAnsi(screen.render(80).join("\n"))).toContain("Enter a number");
    expect(save).not.toHaveBeenCalled();
    screen.handleInput(ESCAPE);
    screen.handleInput(ESCAPE);
    screen.handleInput(ESCAPE);
    await expect(result).resolves.toBeNull();
  });

  it("reverts the displayed value and keeps the current policy when a save fails", async () => {
    const { ui, overlay } = fakeUi();
    const presenter = new PiCommandPresenter(
      ui,
      { setText: vi.fn() } as unknown as Editor,
      new ConversationStore(),
      vi.fn(),
    );
    const save = vi.fn<SettingsSaveCallback>().mockResolvedValue(null);
    const result = presenter.chooseSetting(editableSettings(), save);
    const screen = overlay();

    screen.handleInput(ENTER);
    screen.handleInput(DOWN);
    screen.handleInput(ENTER); // cycle boolean false -> true, save fails
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(stripAnsi(screen.render(80).join("\n"))).toContain("RLM · Verbose logging  false"),
    );
    screen.handleInput(ESCAPE);
    screen.handleInput(ESCAPE);
    await expect(result).resolves.toBeNull();
  });
});
