import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";
import type { components } from "@/lib/rlm-api/generated/openapi";

export type LlmProviderProfileResponse = components["schemas"]["LlmProviderProfileResponse"];
export type LlmProviderProfileCreateRequest =
  components["schemas"]["LlmProviderProfileCreateRequest"];
export type LlmProviderProfileUpdateRequest =
  components["schemas"]["LlmProviderProfileUpdateRequest"];
export type LlmModelCatalogResponse = components["schemas"]["LlmModelCatalogResponse"];
export type LlmModelCatalogEntry = components["schemas"]["LlmModelCatalogEntry"];
export type LlmRoleBindingsResponse = components["schemas"]["LlmRoleBindingsResponse"];
export type LlmRoleBindingResponse = components["schemas"]["LlmRoleBindingResponse"];
export type LlmRoleBindingsUpdateRequest = components["schemas"]["LlmRoleBindingsUpdateRequest"];
export type LlmImportEnvResponse = components["schemas"]["LlmImportEnvResponse"];
export type LlmProviderType = LlmProviderProfileResponse["provider_type"];

export function listLlmProfiles(signal?: AbortSignal) {
  return unwrap(
    typedClient.GET("/api/v1/runtime/llm-profiles", { signal: withTimeout(signal) }),
  );
}

export function createLlmProfile(body: LlmProviderProfileCreateRequest, signal?: AbortSignal) {
  return unwrap(
    typedClient.POST("/api/v1/runtime/llm-profiles", { body, signal: withTimeout(signal) }),
  );
}

export function updateLlmProfile(
  profileId: string,
  body: LlmProviderProfileUpdateRequest,
  signal?: AbortSignal,
) {
  return unwrap(
    typedClient.PATCH("/api/v1/runtime/llm-profiles/{profile_id}", {
      params: { path: { profile_id: profileId } },
      body,
      signal: withTimeout(signal),
    }),
  );
}

export function deleteLlmProfile(profileId: string, signal?: AbortSignal) {
  return unwrap(
    typedClient.DELETE("/api/v1/runtime/llm-profiles/{profile_id}", {
      params: { path: { profile_id: profileId } },
      signal: withTimeout(signal),
    }),
  );
}

export function fetchLlmProfileModels(profileId: string, refresh = false, signal?: AbortSignal) {
  return unwrap(
    typedClient.GET("/api/v1/runtime/llm-profiles/{profile_id}/models", {
      params: { path: { profile_id: profileId }, query: { refresh } },
      signal: withTimeout(signal),
    }),
  );
}

export function testLlmProfile(profileId: string, signal?: AbortSignal) {
  return unwrap(
    typedClient.POST("/api/v1/runtime/llm-profiles/{profile_id}/test", {
      params: { path: { profile_id: profileId } },
      signal: withTimeout(signal),
    }),
  );
}

export function fetchLlmRoleBindings(signal?: AbortSignal) {
  return unwrap(
    typedClient.GET("/api/v1/runtime/llm-roles", { signal: withTimeout(signal) }),
  );
}

export function patchLlmRoleBindings(body: LlmRoleBindingsUpdateRequest, signal?: AbortSignal) {
  return unwrap(
    typedClient.PATCH("/api/v1/runtime/llm-roles", { body, signal: withTimeout(signal) }),
  );
}

export function importLlmProfilesFromEnv(signal?: AbortSignal) {
  return unwrap(
    typedClient.POST("/api/v1/runtime/llm-profiles/import-env", { signal: withTimeout(signal) }),
  );
}
