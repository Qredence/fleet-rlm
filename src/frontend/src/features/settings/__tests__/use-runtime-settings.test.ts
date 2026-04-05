import { describe, expect, it } from "vite-plus/test";

import {
  computeLmRuntimeUpdates,
  computeRuntimeUpdates,
} from "@/features/settings/use-runtime-settings";

describe("computeRuntimeUpdates", () => {
  it("returns an empty update payload when values are unchanged", () => {
    const baseline = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      SECRET_NAME: "LITELLM",
      VOLUME_NAME: "rlm-volume-dspy",
    };

    expect(computeRuntimeUpdates({ ...baseline }, baseline)).toEqual({});
  });

  it("includes changed non-secret values in the update payload", () => {
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

  it("keeps explicit empty-string updates for cleared non-secret values", () => {
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

  it("does not include secret keys unless explicitly rotated or cleared", () => {
    const baseline = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      DSPY_LLM_API_KEY: "sk-...yz",
    };
    const current = {
      DSPY_LM_MODEL: "openai/gpt-4o-mini",
      DSPY_LLM_API_KEY: "",
    };

    expect(computeRuntimeUpdates(current, baseline)).toEqual({});
  });

  it("includes secret keys when a new secret is entered", () => {
    const updates = computeRuntimeUpdates(
      {},
      {},
      {
        secretInputs: {
          DSPY_LLM_API_KEY: "sk-new",
        },
      },
    );
    expect(updates).toEqual({
      DSPY_LLM_API_KEY: "sk-new",
    });
  });

  it("includes explicit secret clear operations", () => {
    const updates = computeRuntimeUpdates(
      {},
      {},
      {
        clearedSecrets: ["DSPY_LLM_API_KEY"],
      },
    );
    expect(updates).toEqual({
      DSPY_LLM_API_KEY: "",
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
    };
    const current = {
      DSPY_LM_MODEL: "",
      DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-mini",
      DSPY_DELEGATE_LM_SMALL_MODEL: "",
      DSPY_LM_API_BASE: "",
    };

    expect(computeLmRuntimeUpdates(current, baseline)).toEqual({
      DSPY_LM_MODEL: "",
      DSPY_DELEGATE_LM_SMALL_MODEL: "",
      DSPY_LM_API_BASE: "",
    });
  });

  it("includes LM API key only when explicitly rotated", () => {
    const updates = computeLmRuntimeUpdates(
      {},
      {},
      {
        secretInputs: {
          DSPY_LLM_API_KEY: "sk-rotated",
        },
      },
    );
    expect(updates).toEqual({
      DSPY_LLM_API_KEY: "sk-rotated",
    });
  });

  it("includes LM API key when explicitly cleared", () => {
    const updates = computeLmRuntimeUpdates(
      {},
      {},
      {
        clearedSecrets: ["DSPY_LLM_API_KEY"],
      },
    );
    expect(updates).toEqual({
      DSPY_LLM_API_KEY: "",
    });
  });
});
