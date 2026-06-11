import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createLlmProfile,
  deleteLlmProfile,
  fetchLlmProfileModels,
  fetchLlmRoleBindings,
  importLlmProfilesFromEnv,
  listLlmProfiles,
  patchLlmRoleBindings,
  testLlmProfile,
  updateLlmProfile,
  type LlmProviderProfileCreateRequest,
  type LlmProviderProfileUpdateRequest,
  type LlmRoleBindingsUpdateRequest,
} from "@/lib/rlm-api/llm-profiles";

export const llmProfilesQueryKey = ["runtime", "llm-profiles"] as const;
export const llmRolesQueryKey = ["runtime", "llm-roles"] as const;

export function profileModelsQueryKey(profileId: string | null | undefined) {
  return ["runtime", "llm-profiles", profileId, "models"] as const;
}

export function useLlmProfiles() {
  return useQuery({
    queryKey: llmProfilesQueryKey,
    queryFn: ({ signal }) => listLlmProfiles(signal),
  });
}

export function useLlmRoleBindings() {
  return useQuery({
    queryKey: llmRolesQueryKey,
    queryFn: ({ signal }) => fetchLlmRoleBindings(signal),
  });
}

export function useLlmProfileModels(profileId: string | null | undefined) {
  return useQuery({
    queryKey: profileModelsQueryKey(profileId),
    queryFn: ({ signal }) => fetchLlmProfileModels(profileId!, false, signal),
    enabled: Boolean(profileId),
    staleTime: 5 * 60 * 1000,
  });
}

export function useLlmProfilesMutations() {
  const queryClient = useQueryClient();

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: llmProfilesQueryKey }),
      queryClient.invalidateQueries({ queryKey: llmRolesQueryKey }),
      queryClient.invalidateQueries({ queryKey: ["runtime", "status"] }),
    ]);
  };

  const createProfile = useMutation({
    mutationFn: (body: LlmProviderProfileCreateRequest) => createLlmProfile(body),
    onSuccess: invalidateAll,
  });

  const saveProfile = useMutation({
    mutationFn: ({ profileId, body }: { profileId: string; body: LlmProviderProfileUpdateRequest }) =>
      updateLlmProfile(profileId, body),
    onSuccess: invalidateAll,
  });

  const removeProfile = useMutation({
    mutationFn: (profileId: string) => deleteLlmProfile(profileId),
    onSuccess: invalidateAll,
  });

  const saveRoleBindings = useMutation({
    mutationFn: (body: LlmRoleBindingsUpdateRequest) => patchLlmRoleBindings(body),
    onSuccess: invalidateAll,
  });

  const importFromEnv = useMutation({
    mutationFn: () => importLlmProfilesFromEnv(),
    onSuccess: invalidateAll,
  });

  const testProfile = useMutation({
    mutationFn: (profileId: string) => testLlmProfile(profileId),
  });

  const refreshProfileModels = useMutation({
    mutationFn: (profileId: string) => fetchLlmProfileModels(profileId, true),
    onSuccess: (_data, profileId) => {
      queryClient.setQueryData(profileModelsQueryKey(profileId), _data);
    },
  });

  return {
    createProfile,
    saveProfile,
    removeProfile,
    saveRoleBindings,
    importFromEnv,
    testProfile,
    refreshProfileModels,
  };
}
