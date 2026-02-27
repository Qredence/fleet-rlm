import { describe, expect, it } from "vitest";

import {
  computeLmRuntimeUpdates,
  computeRuntimeUpdates,
} from "@/features/settings/useRuntimeSettings";

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

describe("computeLmRuntimeUpdates", () => {
  it("includes LM/delegate model and LiteLLM endpoint/key changes", () => {
    const baseline = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-mini",
      DSPY_DELEGATE_LM_SMALL_MODEL: "openai/gpt-4o-mini",
      DSPY_LM_API_BASE: "https://proxy.example/v1",
      DSPY_LLM_API_KEY: "sk-old",
      SECRET_NAME: "LITELLM",
      MODAL_TOKEN_ID: "modal-id",
    };
    const current = {
      ...baseline,
      DSPY_LM_MODEL: "openai/gpt-4.1",
      DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-nano",
      DSPY_DELEGATE_LM_SMALL_MODEL: "openai/gpt-4o-mini",
      DSPY_LM_API_BASE: "https://proxy2.example/v1",
      SECRET_NAME: "SHOULD_NOT_BE_INCLUDED",
      MODAL_TOKEN_ID: "should-not-be-included",
    };

    expect(computeLmRuntimeUpdates(current, baseline)).toEqual({
      DSPY_LM_MODEL: "openai/gpt-4.1",
      DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-nano",
      DSPY_LM_API_BASE: "https://proxy2.example/v1",
    });
  });

  it("keeps explicit empty-string updates for cleared LM values", () => {
    const baseline = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-mini",
      DSPY_DELEGATE_LM_SMALL_MODEL: "openai/gpt-4o-mini",
      DSPY_LM_API_BASE: "https://proxy.example/v1",
      DSPY_LLM_API_KEY: "sk-live",
    };
    const current = {
      DSPY_LM_MODEL: "",
      DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-mini",
      DSPY_DELEGATE_LM_SMALL_MODEL: "",
      DSPY_LM_API_BASE: "",
      DSPY_LLM_API_KEY: "sk-live",
    };

    expect(computeLmRuntimeUpdates(current, baseline)).toEqual({
      DSPY_LM_MODEL: "",
      DSPY_DELEGATE_LM_SMALL_MODEL: "",
      DSPY_LM_API_BASE: "",
    });
  });
});
