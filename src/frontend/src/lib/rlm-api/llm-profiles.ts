import { rlmApiClient } from "@/lib/rlm-api/client";
import type { components } from "@/lib/rlm-api/generated/openapi";

export type LlmProviderProfileResponse = components["schemas"]["LlmProviderProfileResponse"];
export type LlmProviderProfileCreateRequest = components["schemas"]["LlmProviderProfileCreateRequest"];
export type LlmProviderProfileUpdateRequest = components["schemas"]["LlmProviderProfileUpdateRequest"];
export type LlmModelCatalogResponse = components["schemas"]["LlmModelCatalogResponse"];
export type LlmModelCatalogEntry = components["schemas"]["LlmModelCatalogEntry"];
export type LlmRoleBindingsResponse = components["schemas"]["LlmRoleBindingsResponse"];
export type LlmRoleBindingResponse = components["schemas"]["LlmRoleBindingResponse"];
export type LlmRoleBindingsUpdateRequest = components["schemas"]["LlmRoleBindingsUpdateRequest"];
export type LlmImportEnvResponse = components["schemas"]["LlmImportEnvResponse"];
export type LlmProviderType = LlmProviderProfileResponse["provider_type"];

const BASE = "/api/v1/runtime";

export function listLlmProfiles(signal?: AbortSignal) {
  return rlmApiClient.get<LlmProviderProfileResponse[]>(`${BASE}/llm-profiles`, signal);
}

export function createLlmProfile(body: LlmProviderProfileCreateRequest, signal?: AbortSignal) {
  return rlmApiClient.post<LlmProviderProfileResponse>(`${BASE}/llm-profiles`, body, signal);
}

export function updateLlmProfile(
  profileId: string,
  body: LlmProviderProfileUpdateRequest,
  signal?: AbortSignal,
) {
  return rlmApiClient.patch<LlmProviderProfileResponse>(
    `${BASE}/llm-profiles/${profileId}`,
    body,
    signal,
  );
}

export function deleteLlmProfile(profileId: string, signal?: AbortSignal) {
  return rlmApiClient.delete<void>(`${BASE}/llm-profiles/${profileId}`, signal);
}

export function fetchLlmProfileModels(profileId: string, refresh = false, signal?: AbortSignal) {
  const suffix = refresh ? "?refresh=true" : "";
  return rlmApiClient.get<LlmModelCatalogResponse>(
    `${BASE}/llm-profiles/${profileId}/models${suffix}`,
    signal,
  );
}

export function testLlmProfile(profileId: string, signal?: AbortSignal) {
  return rlmApiClient.post(`${BASE}/llm-profiles/${profileId}/test`, undefined, signal);
}

export function fetchLlmRoleBindings(signal?: AbortSignal) {
  return rlmApiClient.get<LlmRoleBindingsResponse>(`${BASE}/llm-roles`, signal);
}

export function patchLlmRoleBindings(body: LlmRoleBindingsUpdateRequest, signal?: AbortSignal) {
  return rlmApiClient.patch<LlmRoleBindingsResponse>(`${BASE}/llm-roles`, body, signal);
}

export function importLlmProfilesFromEnv(signal?: AbortSignal) {
  return rlmApiClient.post<LlmImportEnvResponse>(`${BASE}/llm-profiles/import-env`, undefined, signal);
}
