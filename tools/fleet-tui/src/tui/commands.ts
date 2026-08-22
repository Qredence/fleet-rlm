/**
 * Slash command registry facade for the Fleet TUI.
 *
 * Importing this module registers every built-in command exactly once;
 * `cli-core`, `autocomplete`, and `application` rely on that import-time side
 * effect. The registry itself lives in `commands/registry.ts`, shared handler
 * helpers in `commands/shared.ts`, and the handlers in the feature modules
 * under `commands/`; this file owns their registration order (which is the
 * `/help` listing order) and re-exports the stable public surface.
 */

import { registerCommand } from "./commands/registry.js";
import {
  artifactCommand,
  artifactsCommand,
  attachCommand,
  fileCommand,
  filesCommand,
  volumeCommand,
} from "./commands/files-artifacts.js";
import {
  reloadCommand,
  renameCommand,
  resumeCommand,
  sessionsCommand,
} from "./commands/sessions.js";
import {
  profilesCommand,
  settingsCommand,
  skillCommand,
  skillsCommand,
} from "./commands/skills-settings.js";
import {
  cancelCommand,
  clearCommand,
  exitCommand,
  helpCommand,
  redoCommand,
  statusCommand,
  themeCommand,
  traceCommand,
} from "./commands/status-theme-misc.js";

export {
  getCommand,
  listCommands,
  parseInput,
  registerCommand,
} from "./commands/registry.js";
export type {
  CommandContext,
  CommandHandler,
  CommandPresenter,
  CommandSpec,
  ParsedInput,
  SettingsSaveCallback,
  SettingsUpdate,
} from "./commands/registry.js";
export { formatVolumeTree } from "./commands/files-artifacts.js";

for (const spec of [
  helpCommand,
  clearCommand,
  sessionsCommand,
  renameCommand,
  resumeCommand,
  cancelCommand,
  skillsCommand,
  skillCommand,
  settingsCommand,
  profilesCommand,
  volumeCommand,
  statusCommand,
  attachCommand,
  filesCommand,
  fileCommand,
  artifactCommand,
  artifactsCommand,
  redoCommand,
  reloadCommand,
  traceCommand,
  themeCommand,
  exitCommand,
]) {
  registerCommand(spec);
}
