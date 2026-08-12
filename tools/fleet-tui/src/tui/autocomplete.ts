import {
  type AutocompleteItem,
  type AutocompleteProvider,
  type AutocompleteSuggestions,
  CombinedAutocompleteProvider,
  type SlashCommand,
} from "@earendil-works/pi-tui";

import type { FleetApiClient } from "../fleet-api-client.js";
import { listCommands } from "./commands.js";

export class FleetAutocompleteProvider implements AutocompleteProvider {
  readonly triggerCharacters = ["/"];
  private readonly delegate: CombinedAutocompleteProvider;

  constructor(client: FleetApiClient) {
    const argumentCompletions: Partial<
      Record<string, NonNullable<SlashCommand["getArgumentCompletions"]>>
    > = {
      resume: async (prefix) => sessionItems(client, prefix),
      skill: async (prefix) => skillItems(client, prefix),
    };
    const commands: SlashCommand[] = listCommands().map((spec) => ({
      name: spec.name,
      description: spec.description,
      argumentHint: spec.usage.replace(`/${spec.name}`, "").trim() || undefined,
      getArgumentCompletions: argumentCompletions[spec.name],
    }));
    this.delegate = new CombinedAutocompleteProvider(commands, process.cwd(), null);
  }

  async getSuggestions(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    options: { signal: AbortSignal; force?: boolean },
  ): Promise<AutocompleteSuggestions | null> {
    const line = lines[cursorLine] ?? "";
    if (!line.slice(0, cursorCol).startsWith("/")) return null;
    return this.delegate.getSuggestions(lines, cursorLine, cursorCol, options);
  }

  applyCompletion(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    item: AutocompleteItem,
    prefix: string,
  ): { lines: string[]; cursorLine: number; cursorCol: number } {
    return this.delegate.applyCompletion(lines, cursorLine, cursorCol, item, prefix);
  }

  shouldTriggerFileCompletion(): boolean {
    return false;
  }
}

async function sessionItems(client: FleetApiClient, prefix: string): Promise<AutocompleteItem[]> {
  try {
    const response = await client.listSessions({ limit: 20, search: prefix || undefined });
    return response.items.map((session) => ({
      value: session.id,
      label: session.id,
      description: `${session.title} · ${session.status}`,
    }));
  } catch {
    return [];
  }
}

async function skillItems(client: FleetApiClient, prefix: string): Promise<AutocompleteItem[]> {
  try {
    return (await client.listSkills())
      .filter((skill) => skill.name.toLowerCase().includes(prefix.toLowerCase()))
      .map((skill) => ({
        value: skill.name,
        label: skill.name,
        description: `${skill.version} · ${skill.description}`,
      }));
  } catch {
    return [];
  }
}
