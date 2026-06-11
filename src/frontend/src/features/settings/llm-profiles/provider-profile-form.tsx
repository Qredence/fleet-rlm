import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
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
        onError: (error) => toast.error("Failed to create profile", { description: errorMessage(error) }),
      },
    );
  };

  return (
    <Field className="gap-4 border-0 py-0">
      <div className="flex w-full flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <FieldLabel htmlFor="llm-profile-name">Profile name</FieldLabel>
          <Input
            id="llm-profile-name"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            placeholder="Profile name"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <FieldLabel htmlFor="llm-profile-provider">Provider type</FieldLabel>
          <Select
            value={draftProviderType}
            onValueChange={(value) => {
              const next = value as LlmProviderType;
              setDraftProviderType(next);
              const option = PROVIDER_OPTIONS.find((entry) => entry.id === next);
              if (option) setDraftApiBase(option.defaultBase);
            }}
          >
            <SelectTrigger id="llm-profile-provider" className="w-full" aria-label="Provider type">
              <SelectValue placeholder="Provider type">{selectedProviderLabel}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {PROVIDER_OPTIONS.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <FieldLabel htmlFor="llm-profile-api-base">API base URL</FieldLabel>
          <Input
            id="llm-profile-api-base"
            value={draftApiBase}
            onChange={(event) => setDraftApiBase(event.target.value)}
            placeholder="API base URL"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <FieldLabel htmlFor="llm-profile-api-key">API key</FieldLabel>
          <InputGroup>
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
                size="sm"
                variant="outline"
                className="h-full rounded-none border-y-0 border-r-0 border-l border-border-subtle/70 px-4 shadow-none"
                aria-pressed={showApiKey}
                onClick={() => setShowApiKey((current) => !current)}
              >
                {showApiKey ? "Hide" : "Show"}
              </InputGroupButton>
            </InputGroupAddon>
          </InputGroup>
        </div>
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
  );
}
