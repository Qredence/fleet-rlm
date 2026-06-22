import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectPositioner,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { LlmModelCatalogEntry, LlmProviderProfileResponse } from "@/lib/rlm-api/llm-profiles";

import { CompactField, DEFAULT_SELECT_VALUE } from "./form-field";
import type { OptimizationRunFormState } from "../optimization-model";

export function ReflectionSection({
  form,
  updateForm,
  onReflectionProfileChange,
  profiles,
  profilesLoading,
  modelOptions,
  modelsPending,
  isSubmitting,
}: {
  form: OptimizationRunFormState;
  updateForm: <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => void;
  onReflectionProfileChange: (profileId: string) => void;
  profiles: LlmProviderProfileResponse[];
  profilesLoading: boolean;
  modelOptions: LlmModelCatalogEntry[];
  modelsPending: boolean;
  isSubmitting: boolean;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <CompactField label="Reflection Profile">
        <Select
          value={form.reflectionProfileId || DEFAULT_SELECT_VALUE}
          onValueChange={(value) =>
            onReflectionProfileChange(value === DEFAULT_SELECT_VALUE || !value ? "" : value)
          }
          disabled={isSubmitting || profilesLoading}
        >
          <SelectTrigger className="w-full">
            <SelectValue>
              {profiles.find((profile) => profile.id === form.reflectionProfileId)?.name ??
                "Default reflection model"}
            </SelectValue>
          </SelectTrigger>
          <SelectPositioner align="start">
            <SelectContent className="border-border">
              <SelectGroup>
                <SelectItem value={DEFAULT_SELECT_VALUE}>Default reflection model</SelectItem>
                {profiles.map((profile) => (
                  <SelectItem key={profile.id} value={profile.id}>
                    {profile.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </SelectPositioner>
        </Select>
      </CompactField>
      <CompactField label="Reflection Model">
        {modelsPending && form.reflectionProfileId ? (
          <Skeleton className="h-9 w-full rounded-md" />
        ) : (
          <Select
            value={form.reflectionModelId}
            onValueChange={(value) => value && updateForm("reflectionModelId", value)}
            disabled={isSubmitting || !form.reflectionProfileId || modelOptions.length === 0}
          >
            <SelectTrigger className="w-full">
              <SelectValue>
                {modelOptions.find((model) => model.id === form.reflectionModelId)?.label ??
                  "Select model"}
              </SelectValue>
            </SelectTrigger>
            <SelectPositioner align="start">
              <SelectContent className="border-border">
                <SelectGroup>
                  {modelOptions.map((model) => (
                    <SelectItem key={model.id} value={model.id}>
                      {model.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </SelectPositioner>
          </Select>
        )}
      </CompactField>
    </div>
  );
}
