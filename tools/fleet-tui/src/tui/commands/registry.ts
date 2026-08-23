/** Slash command registry types and lookup for the Fleet TUI. */

import type {
  FleetApiClient,
  FleetSession,
  FleetSettingsPolicy,
  FleetSkillCard,
} from "../../fleet-api-client.js";
import type { ConversationStore, PendingSkillSelection } from "../store.js";

export type CommandContext = {
  store: ConversationStore;
  client: FleetApiClient;
  cancelActiveRun: () => Promise<void> | void;
  exit: () => void;
  /** Submit a prompt as if typed in the editor (used by /redo). */
  submit?: (text: string) => void;
  presenter?: CommandPresenter;
  /**
   * Interactive-only transient notification (alt-screen flash). Omitted in
   * tests/non-interactive contexts, where success notices fall back to the
   * regular system transcript message.
   */
  notify?: (message: string) => void;
};

export type SettingsUpdate = {
  revision: string;
  scope: string;
  path: string;
  value: string | number | boolean | string[] | null;
};

/**
 * Saves one settings update and returns the freshest policy for the next edit:
 * the PATCH response on success, or a GET-refreshed snapshot after a revision
 * conflict. Returns `null` when the save failed (the callback surfaces the
 * error itself). Never throws.
 */
export type SettingsSaveCallback = (update: SettingsUpdate) => Promise<FleetSettingsPolicy | null>;

export interface CommandPresenter {
  showHelp(commands: CommandSpec[]): void;
  chooseSession(sessions: FleetSession[]): Promise<string | null>;
  chooseSkills(
    skills: FleetSkillCard[],
    current: PendingSkillSelection[],
  ): Promise<PendingSkillSelection[] | null>;
  /**
   * Without `save` (compatibility path for mocks/tests): resolve the first
   * chosen update and close. With `save`: keep the overlay open for successive
   * edits, saving through the callback, and resolve `null` when dismissed.
   */
  chooseSetting(
    settings: FleetSettingsPolicy,
    save?: SettingsSaveCallback,
  ): Promise<SettingsUpdate | null>;
  chooseTheme(themes: string[], current: string | undefined): Promise<string | null>;
  chooseProfile(
    profiles: string[],
    active: string | undefined,
    selected: string | undefined,
  ): Promise<string | null>;
}

export type CommandHandler = (args: string[], ctx: CommandContext) => Promise<void> | void;

export type CommandSpec = {
  name: string;
  description: string;
  usage: string;
  handler: CommandHandler;
};

const commands = new Map<string, CommandSpec>();

/**
 * Registers a command specification by name, replacing any existing command with the same name.
 *
 * @param spec - The command specification to register
 */
export function registerCommand(spec: CommandSpec): void {
  commands.set(spec.name, spec);
}

/**
 * Retrieves a registered command by name.
 *
 * @param name - The command name to look up
 * @returns The matching command specification, or `undefined` if no command is registered under that name
 */
export function getCommand(name: string): CommandSpec | undefined {
  return commands.get(name);
}

/**
 * Lists all registered commands.
 *
 * @returns The registered command specifications
 */
export function listCommands(): CommandSpec[] {
  return Array.from(commands.values());
}

export type ParsedInput =
  | { kind: "command"; spec: CommandSpec; args: string[] }
  | { kind: "message"; text: string }
  | { kind: "unknown-command"; name: string }
  | { kind: "empty" };

/**
 * Classifies raw input as empty input, a message, a registered command, or an unknown command.
 *
 * @param raw - The input text to classify
 * @returns The parsed input classification, including command arguments or the unknown command name when applicable
 */
export function parseInput(raw: string): ParsedInput {
  const text = raw.trim();
  if (!text) return { kind: "empty" };
  if (!text.startsWith("/")) return { kind: "message", text };
  const tokens = text.split(/\s+/);
  const name = tokens[0]?.slice(1) ?? "";
  if (!name) return { kind: "message", text };
  const spec = commands.get(name);
  if (!spec) return { kind: "unknown-command", name };
  return { kind: "command", spec, args: tokens.slice(1) };
}
