import type { LlmProviderType } from "@/lib/rlm-api/llm-profiles";

export const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
export const SETTINGS_SECTION_CLASSNAME = "max-w-content gap-4";

export const PROVIDER_OPTIONS: Array<{ id: LlmProviderType; label: string; defaultBase: string }> =
  [
    { id: "openai_responses", label: "OpenAI Responses", defaultBase: "https://api.openai.com/v1" },
    { id: "openai_chat_completion", label: "OpenAI Chat Completion", defaultBase: "" },
    { id: "anthropic_messages", label: "Anthropic Messages", defaultBase: "https://api.anthropic.com" },
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

const PROVIDER_TYPE_LABELS: Record<LlmProviderType, string> = {
  openai_responses: "OpenAI Responses",
  openai_chat_completion: "OpenAI Chat Completion",
  anthropic_messages: "Anthropic Messages",
};

export function formatProfileLabel(profile: {
  id: string;
  name: string;
  provider_type: LlmProviderType;
  api_base?: string;
}): string {
  const shortId = profile.id.slice(0, 8);
  const base = profile.api_base?.replace(/^https?:\/\//, "") || "default base";
  const typeLabel = PROVIDER_TYPE_LABELS[profile.provider_type] ?? profile.provider_type;
  return `${profile.name} · ${typeLabel} · ${base} · ${shortId}`;
}

function stripNativeModelsPrefix(modelId: string): string {
  return modelId.startsWith("models/") ? modelId.slice("models/".length) : modelId;
}

export function modelMatchesCatalog(modelId: string, catalogId: string): boolean {
  if (!modelId || !catalogId) return false;
  const left = stripNativeModelsPrefix(modelId);
  const right = stripNativeModelsPrefix(catalogId);
  if (left === right) return true;
  return right.endsWith(`/${left}`) || left.endsWith(`/${right}`);
}