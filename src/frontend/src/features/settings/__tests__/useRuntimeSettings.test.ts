import { describe, expect, it } from "vitest";

import { computeRuntimeUpdates } from "@/features/settings/useRuntimeSettings";

describe("computeRuntimeUpdates", () => {
  it("returns an empty update payload when values are unchanged", () => {
    const baseline = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      SECRET_NAME: "LITELLM",
      VOLUME_NAME: "rlm-volume-dspy",
    };

    expect(computeRuntimeUpdates({ ...baseline }, baseline)).toEqual({});
  });

  it("includes changed values in the update payload", () => {
    const baseline = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      SECRET_NAME: "LITELLM",
      VOLUME_NAME: "rlm-volume-dspy",
    };
    const current = {
      ...baseline,
      DSPY_LM_MODEL: "openai/gpt-4.1-mini",
      SECRET_NAME: "ALT_SECRET",
    };

    expect(computeRuntimeUpdates(current, baseline)).toEqual({
      DSPY_LM_MODEL: "openai/gpt-4.1-mini",
      SECRET_NAME: "ALT_SECRET",
    });
  });

  it("keeps explicit empty-string updates for cleared values", () => {
    const baseline = {
      VOLUME_NAME: "rlm-volume-dspy",
    };
    const current = {
      VOLUME_NAME: "",
    };

    expect(computeRuntimeUpdates(current, baseline)).toEqual({
      VOLUME_NAME: "",
    });
  });
});
