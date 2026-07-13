import { describe, expect, it } from "vitest";

import { formatTranscript } from "./transcript.js";

describe("restored Fleet transcript", () => {
  it("renders persisted reasoning, tool trajectories, and Fleet data parts", () => {
    expect(
      formatTranscript([
        { id: "u1", role: "user", parts: [{ type: "text", text: "inspect this" }] },
        {
          id: "a1",
          role: "assistant",
          metadata: { sequence: 2 },
          parts: [
            { type: "reasoning", text: "I will inspect the data." },
            { type: "data-rlm-code", data: { code: "print('ok')" } },
            {
              type: "dynamic-tool",
              toolName: "create_artifact",
              input: { name: "report" },
              output: { id: "a1" },
            },
            { type: "data-artifact", data: { name: "report.csv" } },
            { type: "text", text: "Completed." },
          ],
        },
      ]),
    ).toContain(
      'Reasoning:\nI will inspect the data.\nrlm code:\n{"code":"print(\'ok\')"}\nTool: create_artifact\ninput: {"name":"report"}\noutput: {"id":"a1"}\nartifact:\n{"name":"report.csv"}\nCompleted.',
    );
  });

  it("redacts sensitive fields in durable data", () => {
    expect(
      formatTranscript([
        {
          id: "a1",
          role: "assistant",
          parts: [{ type: "data-usage", data: { api_key: "not-for-display", tokens: 3 } }],
        },
      ]),
    ).toContain('{"api_key":"[redacted]","tokens":3}');
  });
});
