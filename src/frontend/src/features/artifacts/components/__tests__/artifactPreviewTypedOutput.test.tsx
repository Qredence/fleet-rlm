import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ArtifactPreview } from "@/features/artifacts/components/ArtifactPreview";
import type { ExecutionStep } from "@/stores/artifactStore";

describe("ArtifactPreview typed output rendering", () => {
  it("renders typed tool result preview from final payload", () => {
    const steps: ExecutionStep[] = [
      {
        id: "out-tool",
        type: "output",
        label: "Final output",
        output: {
          text: "",
          payload: {
            tool_name: "list_files",
            tool_input: { path: "." },
            tool_output: ["README.md", "src", "tests"],
          },
        },
        timestamp: 1,
      },
    ];

    const html = renderToStaticMarkup(<ArtifactPreview steps={steps} />);
    expect(html).toContain("Tool Result");
    expect(html).toContain("list_files");
    expect(html).toContain("README.md");
  });

  it("renders explicit failure state for error outputs", () => {
    const steps: ExecutionStep[] = [
      {
        id: "out-error",
        type: "output",
        label: "Execution error",
        output: {
          text: "",
          payload: {
            error: {
              message: "ValueError: boom",
              traceback: "Traceback line 1",
            },
          },
        },
        timestamp: 2,
      },
    ];

    const html = renderToStaticMarkup(<ArtifactPreview steps={steps} />);
    expect(html).toContain("Execution failed");
    expect(html).toContain("ValueError: boom");
    expect(html).toContain("Traceback line 1");
  });
});
