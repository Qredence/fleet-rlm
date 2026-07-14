import { Box, Text, renderToString } from "ink";
import { describe, expect, it } from "vitest";

import { TimelineViewport } from "./timeline.js";

describe("timeline viewport", () => {
  it("anchors short timelines to the bottom with spacing between events", () => {
    const output = renderToString(
      <TimelineViewport height={7}>
        <Box><Text>first event</Text></Box>
        <Box><Text>second event</Text></Box>
      </TimelineViewport>,
      { columns: 60 },
    );
    const lines = output.split("\n");

    expect(lines.findIndex((line) => line.includes("first event"))).toBeGreaterThan(0);
    expect(lines.join("\n")).toContain("first event\n\nsecond event");
    expect(lines.at(-1)).toContain("second event");
  });
});
