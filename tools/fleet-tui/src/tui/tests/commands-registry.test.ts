import { describe, expect, it } from "vitest";

import { getCommand, listCommands, parseInput } from "../commands.js";

/** The legacy `/help` listing order, owned by the facade's registration loop. */
const REGISTRATION_ORDER = [
  "help",
  "clear",
  "sessions",
  "rename",
  "resume",
  "cancel",
  "skills",
  "skill",
  "settings",
  "profiles",
  "volume",
  "status",
  "attach",
  "files",
  "file",
  "artifact",
  "artifacts",
  "redo",
  "reload",
  "trace",
  "theme",
  "exit",
];

describe("command registration side effects", () => {
  it("registers every built-in command exactly once, in stable /help order", () => {
    const names = listCommands().map((spec) => spec.name);
    expect(names).toEqual(REGISTRATION_ORDER);
    expect(new Set(names).size).toBe(REGISTRATION_ORDER.length);
  });

  it("routes a registered command through parseInput on the shared registry", () => {
    const parsed = parseInput("/sessions archived runs");
    expect(parsed.kind).toBe("command");
    if (parsed.kind === "command") {
      expect(parsed.spec).toBe(getCommand("sessions"));
      expect(parsed.args).toEqual(["archived", "runs"]);
    }
  });
});
