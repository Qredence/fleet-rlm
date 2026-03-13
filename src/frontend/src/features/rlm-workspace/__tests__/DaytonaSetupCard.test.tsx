import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { SourceSetupCard } from "@/features/rlm-workspace/SourceSetupCard";

describe("SourceSetupCard", () => {
  const baseProps = {
    manualRepoUrl: "",
    onManualRepoUrlChange: vi.fn(),
    contextPaths: "",
    onContextPathsChange: vi.fn(),
    repoRef: "main",
    onRepoRefChange: vi.fn(),
    maxDepth: 2,
    onMaxDepthChange: vi.fn(),
    batchConcurrency: 4,
    onBatchConcurrencyChange: vi.fn(),
    detectedRepoContext: null,
    resolvedRepoContext: null,
    hasInvalidManualOverride: false,
  };

  it("shows the active run repo context without falling back to repository required", () => {
    const html = renderToStaticMarkup(
      <SourceSetupCard
        {...baseProps}
        activeRunRepoUrl="https://github.com/qredence/fleet-rlm"
        activeRunContextSources={[
          {
            sourceId: "ctx-1",
            kind: "directory",
            hostPath: "/Users/zocho/Documents/specs",
          },
        ]}
        isActiveRunContextVisible
      />,
    );

    expect(html).toContain("Active run context");
    expect(html).toContain("Edit source setup");
    expect(html).toContain(
      "The active run is using the current source mix shown above.",
    );
    expect(html).toContain("https://github.com/qredence/fleet-rlm");
    expect(html).not.toContain("Repository required");
  });

  it("defaults to a compact reasoning-only summary", () => {
    const html = renderToStaticMarkup(<SourceSetupCard {...baseProps} />);

    expect(html).toContain("Reasoning only");
    expect(html).toContain("Edit source setup");
    expect(html).toContain(
      "No external sources are configured yet. The runtime will use reasoning-only mode.",
    );
    expect(html).not.toContain("Repository URL");
  });

  it("shows a combined repo and local context badge when both are configured", () => {
    const html = renderToStaticMarkup(
      <SourceSetupCard
        {...baseProps}
        contextPaths={"/Users/zocho/Documents/spec.pdf\n/workspace/docs"}
        resolvedRepoContext={{
          repoUrl: "https://github.com/qredence/fleet-rlm",
          source: "manual",
        }}
      />,
    );

    expect(html).toContain("Repo + local context");
    expect(html).toContain("2 local paths");
  });

  it("keeps invalid manual overrides visible without fully expanding the card", () => {
    const html = renderToStaticMarkup(
      <SourceSetupCard
        {...baseProps}
        manualRepoUrl="not-a-valid-repo-url"
        hasInvalidManualOverride
      />,
    );

    expect(html).toContain("Repository URL needs to be valid");
    expect(html).toContain(
      "Manual repository override needs attention before the next run.",
    );
    expect(html).toContain(
      "Expand source setup to fix the URL or clear the override.",
    );
  });
});
