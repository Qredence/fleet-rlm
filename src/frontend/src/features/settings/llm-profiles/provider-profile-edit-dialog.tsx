import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FieldLabel } from "@/components/ui/field";
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
import type { ProviderProfileSummary } from "./provider-profile-list";

interface ProviderProfileEditDialogProps {
  profile: ProviderProfileSummary;
  writeEnabled: boolean;
  mutations: ReturnType<typeof useLlmProfilesMutations>;
}

export function ProviderProfileEditDialog({
  profile,
  writeEnabled,
  mutations,
}: ProviderProfileEditDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(profile.name);
  const [providerType, setProviderType] = useState<LlmProviderType>(profile.provider_type);
  const [apiBase, setApiBase] = useState(profile.api_base ?? "");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(profile.name);
    setProviderType(profile.provider_type);
    setApiBase(profile.api_base ?? "");
    setApiKey("");
    setShowApiKey(false);
  }, [open, profile]);

  const selectedProviderLabel =
    PROVIDER_OPTIONS.find((option) => option.id === providerType)?.label ?? "";

  const handleSave = () => {
    mutations.saveProfile.mutate(
      {
        profileId: profile.id,
        body: {
          name,
          provider_type: providerType,
          api_base: apiBase,
          ...(apiKey.trim() ? { api_key: apiKey } : {}),
        },
      },
      {
        onSuccess: () => {
          toast.success("Profile updated");
          setOpen(false);
        },
        onError: (error) =>
          toast.error("Failed to update profile", { description: errorMessage(error) }),
      },
    );
  };

  return (
    <>
      <Button variant="outline" size="sm" disabled={!writeEnabled} onClick={() => setOpen(true)}>
        <Pencil className="size-4" />
        Edit
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Edit provider profile</DialogTitle>
            <DialogDescription>
              Update the profile label, provider type, or API base. Leave the API key blank to keep
              the stored secret.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={`edit-profile-name-${profile.id}`}>Profile name</FieldLabel>
              <Input
                id={`edit-profile-name-${profile.id}`}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={`edit-profile-provider-${profile.id}`}>Provider type</FieldLabel>
              <Select
                value={providerType}
                onValueChange={(value) => {
                  const next = value as LlmProviderType;
                  setProviderType(next);
                  const option = PROVIDER_OPTIONS.find((entry) => entry.id === next);
                  if (option && !apiBase.trim()) setApiBase(option.defaultBase);
                }}
              >
                <SelectTrigger id={`edit-profile-provider-${profile.id}`} className="w-full">
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
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={`edit-profile-base-${profile.id}`}>API base URL</FieldLabel>
              <Input
                id={`edit-profile-base-${profile.id}`}
                value={apiBase}
                onChange={(event) => setApiBase(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={`edit-profile-key-${profile.id}`}>API key</FieldLabel>
              <InputGroup>
                <InputGroupInput
                  id={`edit-profile-key-${profile.id}`}
                  type={showApiKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={profile.has_api_key ? "Leave blank to keep current key" : "API key"}
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
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!writeEnabled || mutations.saveProfile.isPending}
              onClick={handleSave}
            >
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
