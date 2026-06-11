import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldLabel, FieldTitle } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useLlmProfileModels,
  type useLlmProfilesMutations,
} from "@/features/settings/use-llm-profiles";

import { SETTINGS_FIELD_CLASSNAME, errorMessage, formatProfileLabel, modelMatchesCatalog } from "./constants";
import type { ProviderProfileSummary } from "./provider-profile-list";

interface RoleModelAssignmentProps {
  role: "planner" | "delegate" | "delegate_small";
  title: string;
  description: string;
  profiles: ProviderProfileSummary[];
  binding?: { profile_id?: string | null; profile_name?: string | null; model_id?: string };
  writeEnabled: boolean;
  mutations: ReturnType<typeof useLlmProfilesMutations>;
}

export function RoleModelAssignment({
  role,
  title,
  description,
  profiles,
  binding,
  writeEnabled,
  mutations,
}: RoleModelAssignmentProps) {
  const [profileId, setProfileId] = useState(binding?.profile_id ?? "");
  const [modelId, setModelId] = useState(binding?.model_id ?? "");
  const modelsQuery = useLlmProfileModels(profileId || null);

  useEffect(() => {
    setProfileId(binding?.profile_id ?? "");
    setModelId(binding?.model_id ?? "");
  }, [binding?.model_id, binding?.profile_id]);

  const selectedProfile = profiles.find((profile) => profile.id === profileId);
  const selectedProfileLabel = selectedProfile
    ? formatProfileLabel(selectedProfile)
    : binding?.profile_name ?? "";

  const catalogModels = modelsQuery.data?.models ?? [];
  const catalogError = modelsQuery.data?.error;
  const selectedModelLabel =
    catalogModels.find((model) => modelMatchesCatalog(modelId, model.id))?.label ?? modelId;

  const isDirty =
    profileId !== (binding?.profile_id ?? "") || modelId !== (binding?.model_id ?? "");

  const isStaleModel = useMemo(() => {
    if (!modelId || modelsQuery.isPending || catalogError || catalogModels.length === 0) {
      return false;
    }
    return !catalogModels.some((model) => modelMatchesCatalog(modelId, model.id));
  }, [catalogError, catalogModels, modelId, modelsQuery.isPending]);

  const saveBinding = () => {
    mutations.saveRoleBindings.mutate(
      {
        [role]: {
          profile_id: profileId || null,
          model_id: modelId,
        },
      },
      {
        onSuccess: () => toast.success(`${title} updated`),
        onError: (error) => toast.error("Failed to update role binding", { description: errorMessage(error) }),
      },
    );
  };

  const profileSelectId = `llm-role-${role}-profile`;
  const modelSelectId = `llm-role-${role}-model`;

  return (
    <Field className={SETTINGS_FIELD_CLASSNAME}>
      <FieldContent>
        <FieldTitle>{title}</FieldTitle>
        <FieldDescription>{description}</FieldDescription>
      </FieldContent>
      <div className="flex w-full flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <FieldLabel htmlFor={profileSelectId}>Provider profile</FieldLabel>
          <Select
            value={profileId}
            onValueChange={(value) => {
              setProfileId(value ?? "");
              setModelId("");
            }}
            disabled={profiles.length === 0}
          >
            <SelectTrigger id={profileSelectId} className="w-full" aria-label="Select provider profile">
              <SelectValue placeholder="Select provider profile">
                {selectedProfileLabel || undefined}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {profiles.map((profile) => (
                <SelectItem key={profile.id} value={profile.id}>
                  {formatProfileLabel(profile)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <FieldLabel htmlFor={modelSelectId}>Model</FieldLabel>
          <div className="flex gap-2">
            {modelsQuery.isPending && profileId ? (
              <Skeleton className="h-9 w-full rounded-md" />
            ) : (
              <Select
                value={modelId}
                onValueChange={(value) => setModelId(value ?? "")}
                disabled={!profileId || modelsQuery.isPending}
              >
                <SelectTrigger id={modelSelectId} className="w-full" aria-label="Select model">
                  <SelectValue placeholder={profileId ? "Select model" : "Choose a profile first"}>
                    {modelId ? selectedModelLabel : undefined}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {catalogModels.map((model) => (
                    <SelectItem key={model.id} value={model.id}>
                      {model.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Button
              variant="outline"
              size="icon"
              disabled={!profileId || mutations.refreshProfileModels.isPending}
              onClick={() => profileId && mutations.refreshProfileModels.mutate(profileId)}
              aria-label="Refresh models"
            >
              <RefreshCw className="size-4" />
            </Button>
          </div>
          {catalogError ? (
            <FieldDescription className="text-destructive">
              Provider catalog error: {catalogError}
            </FieldDescription>
          ) : null}
          {modelsQuery.isError ? (
            <FieldDescription className="text-destructive">
              Failed to load models. Try refresh or check the provider credentials.
            </FieldDescription>
          ) : null}
          {isStaleModel ? (
            <FieldDescription className="text-amber-600 dark:text-amber-400">
              Saved model <span className="font-mono">{modelId}</span> is not in the current catalog. Pick a
              model and save to refresh the binding.
            </FieldDescription>
          ) : null}
          {modelsQuery.isPending && profileId ? (
            <FieldDescription>Loading models…</FieldDescription>
          ) : null}
        </div>
        <Button
          variant="secondary"
          className="self-start rounded-lg"
          disabled={
            !writeEnabled || !profileId || !modelId || !isDirty || mutations.saveRoleBindings.isPending
          }
          onClick={saveBinding}
        >
          Save {title.toLowerCase()}
        </Button>
      </div>
    </Field>
  );
}
