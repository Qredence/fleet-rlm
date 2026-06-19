import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldContent, FieldGroup, FieldLabel, FieldTitle } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectPositioner,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { useLlmProfilesMutations } from "@/features/settings/use-llm-profiles";
import type { LlmProviderType } from "@/lib/rlm-api/llm-profiles";

import { PROVIDER_OPTIONS, errorMessage } from "./constants";

interface ProviderProfileFormProps {
  writeEnabled: boolean;
  mutations: ReturnType<typeof useLlmProfilesMutations>;
}

export function ProviderProfileForm({ writeEnabled, mutations }: ProviderProfileFormProps) {
  const [draftName, setDraftName] = useState("My provider");
  const [draftProviderType, setDraftProviderType] = useState<LlmProviderType>("openai");
  const [draftApiBase, setDraftApiBase] = useState(PROVIDER_OPTIONS[0]?.defaultBase ?? "");
  const [draftApiKey, setDraftApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);

  const selectedProviderLabel =
    PROVIDER_OPTIONS.find((option) => option.id === draftProviderType)?.label ?? "";

  const handleCreateProfile = () => {
    if (!writeEnabled) {
      toast.error("Runtime settings are read-only in this environment");
      return;
    }
    mutations.createProfile.mutate(
      {
        name: draftName,
        provider_type: draftProviderType,
        api_base: draftApiBase,
        api_key: draftApiKey,
      },
      {
        onSuccess: () => {
          setDraftApiKey("");
          toast.success("Provider profile created");
        },
        onError: (error) =>
          toast.error("Failed to create profile", { description: errorMessage(error) }),
      },
    );
  };

  return (
    <FieldGroup className="gap-0">
      <Field orientation="responsive" className="border-b border-border-subtle py-3">
        <FieldContent>
          <FieldLabel htmlFor="llm-profile-name">Profile name</FieldLabel>
        </FieldContent>
        <div className="flex min-w-0 flex-1 justify-start sm:justify-end">
          <Input
            id="llm-profile-name"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            placeholder="Profile name"
            className="w-full min-w-0 sm:max-w-sm"
          />
        </div>
      </Field>
      <Field orientation="responsive" className="border-b border-border-subtle py-3">
        <FieldContent>
          <FieldLabel htmlFor="llm-profile-provider">Provider type</FieldLabel>
        </FieldContent>
        <div className="flex min-w-0 flex-1 justify-start sm:justify-end">
          <Select
            value={draftProviderType}
            onValueChange={(value) => {
              const next = value as LlmProviderType;
              setDraftProviderType(next);
              const option = PROVIDER_OPTIONS.find((entry) => entry.id === next);
              if (option) setDraftApiBase(option.defaultBase);
            }}
          >
            <SelectTrigger
              id="llm-profile-provider"
              className="w-full min-w-0 sm:max-w-sm"
              aria-label="Provider type"
            >
              <SelectValue placeholder="Provider type">{selectedProviderLabel}</SelectValue>
            </SelectTrigger>
            <SelectPositioner>
              <SelectContent>
                {PROVIDER_OPTIONS.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </SelectPositioner>
          </Select>
        </div>
      </Field>
      <Field orientation="responsive" className="border-b border-border-subtle py-3">
        <FieldContent>
          <FieldLabel htmlFor="llm-profile-api-base">API base URL</FieldLabel>
        </FieldContent>
        <div className="flex min-w-0 flex-1 justify-start sm:justify-end">
          <Input
            id="llm-profile-api-base"
            value={draftApiBase}
            onChange={(event) => setDraftApiBase(event.target.value)}
            placeholder="API base URL"
            className="w-full min-w-0 sm:max-w-sm"
          />
        </div>
      </Field>
      <Field orientation="responsive" className="border-b border-border-subtle py-3">
        <FieldContent>
          <FieldLabel htmlFor="llm-profile-api-key">API key</FieldLabel>
        </FieldContent>
        <div className="flex min-w-0 flex-1 justify-start sm:justify-end">
          <InputGroup className="w-full min-w-0 sm:max-w-sm">
            <InputGroupInput
              id="llm-profile-api-key"
              type={showApiKey ? "text" : "password"}
              value={draftApiKey}
              onChange={(event) => setDraftApiKey(event.target.value)}
              placeholder="API key"
              autoComplete="off"
            />
            <InputGroupAddon align="inline-end">
              <InputGroupButton
                type="button"
                aria-pressed={showApiKey}
                onClick={() => setShowApiKey((current) => !current)}
              >
                {showApiKey ? "Hide" : "Show"}
              </InputGroupButton>
            </InputGroupAddon>
          </InputGroup>
        </div>
      </Field>
      <Field orientation="responsive" className="py-3">
        <FieldContent className="sr-only">
          <FieldTitle>Create provider profile</FieldTitle>
        </FieldContent>
        <div className="flex min-w-0 flex-1 justify-start sm:justify-end">
          <Button
            className="self-start rounded-lg"
            disabled={!writeEnabled || mutations.createProfile.isPending || !draftApiKey.trim()}
            onClick={handleCreateProfile}
          >
            <Plus className="size-4" />
            Add profile
          </Button>
        </div>
      </Field>
    </FieldGroup>
  );
}
