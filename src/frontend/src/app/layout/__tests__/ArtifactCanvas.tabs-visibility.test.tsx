import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { ArtifactCanvas } from "@/components/domain/artifacts/ArtifactCanvas";
import { useArtifactStore } from "@/stores/artifactStore";

describe("ArtifactCanvas tab visibility", () => {
  beforeEach(() => {
    useArtifactStore.getState().clear();
  });

  it("renders a stable tab-list height so labels are fully visible", () => {
    const html = renderToStaticMarkup(<ArtifactCanvas />);

    expect(html).toContain('data-slot="tabs-list"');
    expect(html).toMatch(/style="[^"]*height:2rem;[^"]*min-height:2rem[^"]*"/);
  });
});
