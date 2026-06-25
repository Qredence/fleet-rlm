import type { LlmProviderType } from "@/lib/rlm-api/llm-profiles";

export const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
export const SETTINGS_SECTION_CLASSNAME = "max-w-content gap-4";

export const PROVIDER_OPTIONS: Array<{ id: LlmProviderType; label: string; defaultBase: string }> =
  [
    { id: "openai", label: "OpenAI", defaultBase: "https://api.openai.com/v1" },
    { id: "anthropic", label: "Anthropic", defaultBase: "https://api.anthropic.com" },
    {
      id: "google",
      label: "Google Gemini",
      defaultBase: "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    { id: "openai_compatible", label: "OpenAI-compatible (vLLM, Ollama, …)", defaultBase: "" },
    { id: "litellm_proxy", label: "LiteLLM proxy", defaultBase: "" },
    { id: "anthropic_compatible", label: "Anthropic-compatible (POST /v1/messages)", defaultBase: "" },
  ];

export const ROLE_ROWS = [
  {
    role: "planner" as const,
    title: "Planner model",
    description: "Primary model for chat turns and planning.",
  },
  {
    role: "delegate" as const,
    title: "Delegate model",
    description: "Model for recursive or long-context sub-agent tasks.",
  },
  {
    role: "delegate_small" as const,
    title: "Delegate small model",
    description: "Lightweight delegate model for fast/low-cost operations.",
  },
];

export { errorMessage } from "../runtime-status-panel";

export function formatProfileLabel(profile: {
  id: string;
  name: string;
  provider_type: string;
  api_base?: string;
}): string {
  const shortId = profile.id.slice(0, 8);
  const base = profile.api_base?.replace(/^https?:\/\//, "") || "default base";
  return `${profile.name} · ${profile.provider_type} · ${base} · ${shortId}`;
}

function stripGoogleNativeModelPrefix(modelId: string): string {
  return modelId.startsWith("models/") ? modelId.slice("models/".length) : modelId;
}

export function modelMatchesCatalog(modelId: string, catalogId: string): boolean {
  if (!modelId || !catalogId) return false;
  const left = stripGoogleNativeModelPrefix(modelId);
  const right = stripGoogleNativeModelPrefix(catalogId);
  if (left === right) return true;
  return right.endsWith(`/${left}`) || left.endsWith(`/${right}`);
}
