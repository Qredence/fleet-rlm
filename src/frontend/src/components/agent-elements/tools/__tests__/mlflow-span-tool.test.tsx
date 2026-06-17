import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { MlflowSpanTool } from "@/components/agent-elements/tools/mlflow-span-tool";

describe("MlflowSpanTool", () => {
  it("renders span metadata, trace link, and bounded details", () => {
    const html = renderToStaticMarkup(
      <MlflowSpanTool
        defaultOpen
        part={{
          type: "tool-MlflowSpan",
          toolCallId: "message-1:mlflow_span:span-1",
          state: "output-available",
          title: "Planner model",
          input: { prompt: "hello", api_key: "<redacted>" },
          output: { text: "done" },
          mlflowSpan: {
            spanId: "span-1",
            traceId: "trace-1",
            status: "completed",
            durationMs: 1200,
            trackingUri: "http://127.0.0.1:5001",
            experimentId: "exp-1",
          },
        }}
      />,
    );

    expect(html).toContain("Planner model");
    expect(html).toContain("completed - 1.2s");
    expect(html).toContain("trace-1");
    expect(html).toContain("&lt;redacted&gt;");
    expect(html).toContain("done");
  });
});
