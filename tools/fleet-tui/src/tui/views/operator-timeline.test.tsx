import { renderToString } from "ink";
import { describe, expect, it } from "vitest";

import { visibleLength } from "../format.js";
import type { Message } from "../store.js";
import { Code } from "./Code.js";
import { Result } from "./Result.js";

const result: Extract<Message, { kind: "result" }> = {
  id: "result-1",
  kind: "result",
  runId: "run-1",
  schemaId: "answer",
  schemaVersion: "1",
  value: { digit: "7" },
  narrative: "The requested digit is **7**.",
  ts: 1,
};

describe("operator timeline views", () => {
  it.each([40, 80, 120])("renders a structured result within %d columns", (columns) => {
    const output = renderToString(
      <Result message={result} width={columns} expanded focused={false} />,
      { columns },
    );
    expect(output).toContain("RESULT");
    expect(output).toContain("7");
    expect(output.split("\n").every((line) => visibleLength(line) <= columns)).toBe(true);
  });

  it("renders readable code without a line-number gutter", () => {
    const code: Extract<Message, { kind: "code" }> = {
      id: "code-1",
      kind: "code",
      runId: "run-1",
      step: 1,
      code: "answer = '7'\nprint(f'{answer}')",
      ts: 1,
    };
    const output = renderToString(<Code message={code} width={60} expanded focused={false} />, {
      columns: 60,
    });
    expect(output).toContain("answer = '7'");
    expect(output).toContain("print(f'{answer}')");
    expect(output).not.toMatch(/^\s*\d+\s+│/m);
  });
});
