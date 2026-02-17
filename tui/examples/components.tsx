/**
 * Example usage of OpenTUI components
 * Run with: bun run examples/components.tsx
 */

import { createCliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";
import { useState } from "react";
import {
  Spinner,
  createPulse,
  createWave,
  StreamDown,
  Search,
  SearchInput,
  type SearchOptions,
} from "../src/components";

function ExampleApp() {
  const [searchOptions, setSearchOptions] = useState<SearchOptions>({
    pattern: "",
    caseSensitive: false,
    regex: false,
  });

  return (
    <box flexDirection="column" padding={2} gap={2}>
      <box border borderStyle="rounded" title=" Spinner Examples ">
        <box flexDirection="row" gap={4} padding={1}>
          <box>
            <text>Default (dots):</text>
            <Spinner />
          </box>

          <box>
            <text>Bouncing Ball:</text>
            <Spinner name="bouncingBall" color="cyan" />
          </box>

          <box>
            <text>Moon:</text>
            <Spinner name="moon" />
          </box>

          <box>
            <text>Pulse:</text>
            <Spinner
              name="dots"
              color={createPulse(["red", "yellow", "green", "cyan", "blue", "magenta"])}
            />
          </box>

          <box>
            <text>Wave:</text>
            <Spinner
              name="line"
              color={createWave(["#ff0000", "#00ff00", "#0000ff"])}
            />
          </box>
        </box>
      </box>

      <box border borderStyle="rounded" title=" StreamDown Example ">
        <box padding={1}>
          <StreamDown
            content="# Hello World\n\nThis is **bold** and *italic* text.\n\n- Item 1\n- Item 2\n- Item 3\n\n`code inline` and:\n\n```typescript\nconst x = 1;\n```"
            speed={30}
            isStreaming={true}
          />
        </box>
      </box>

      <box border borderStyle="rounded" title=" Search Example ">
        <box flexDirection="column" padding={1} gap={1}>
          <SearchInput
            onSearch={(options) => setSearchOptions(options)}
            placeholder="Search files..."
          />
          {searchOptions.pattern && (
            <Search options={searchOptions} />
          )}
        </box>
      </box>
    </box>
  );
}

async function main() {
  const renderer = await createCliRenderer();
  const root = createRoot(renderer);

  root.render(<ExampleApp />);
}

main().catch(console.error);
