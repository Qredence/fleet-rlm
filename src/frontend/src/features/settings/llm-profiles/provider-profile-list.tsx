import { useState } from "react";
import { Bot, Trash2 } from "lucide-react";
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
import { Field, FieldContent, FieldDescription, FieldTitle } from "@/components/ui/field";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import type { useLlmProfilesMutations } from "@/features/settings/use-llm-profiles";
import type { LlmProviderType } from "@/lib/rlm-api/llm-profiles";

import { SETTINGS_FIELD_CLASSNAME, errorMessage, formatProfileLabel } from "./constants";
import { ProviderProfileEditDialog } from "./provider-profile-edit-dialog";

export interface ProviderProfileSummary {
  id: string;
  name: string;
  provider_type: LlmProviderType;
  api_base?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
}

interface ProviderProfileListProps {
  profiles: ProviderProfileSummary[];
  writeEnabled: boolean;
  mutations: ReturnType<typeof useLlmProfilesMutations>;
}

export function ProviderProfileList({
  profiles,
  writeEnabled,
  mutations,
}: ProviderProfileListProps) {
  if (profiles.length === 0) {
    return (
      <SectionCard variant="subtle">
        <SectionCardHeader>
          <SectionCardTitle>Provider profiles</SectionCardTitle>
          <SectionCardDescription>
            Import from .env or add a profile below to start assigning models per role.
          </SectionCardDescription>
        </SectionCardHeader>
      </SectionCard>
    );
  }

  return (
    <SectionCard variant="subtle">
      <SectionCardHeader className="border-b border-border-subtle/70 pb-4">
        <SectionCardTitle>Provider profiles</SectionCardTitle>
        <SectionCardDescription>
          {profiles.length} saved profile{profiles.length === 1 ? "" : "s"}. Profiles with the same
          name are disambiguated by provider, API base, and id.
        </SectionCardDescription>
      </SectionCardHeader>
      <SectionCardContent className="flex flex-col gap-0 pt-2">
        {profiles.map((profile) => (
          <ProfileCard
            key={profile.id}
            profile={profile}
            writeEnabled={writeEnabled}
            mutations={mutations}
          />
        ))}
      </SectionCardContent>
    </SectionCard>
  );
}

function ProfileCard({
  profile,
  writeEnabled,
  mutations,
}: {
  profile: ProviderProfileSummary;
  writeEnabled: boolean;
  mutations: ReturnType<typeof useLlmProfilesMutations>;
}) {
  const [deleteOpen, setDeleteOpen] = useState(false);

  const handleDelete = () => {
    mutations.removeProfile.mutate(profile.id, {
      onSuccess: () => {
        toast.success("Profile deleted");
        setDeleteOpen(false);
      },
      onError: (error) => toast.error("Delete failed", { description: errorMessage(error) }),
    });
  };

  return (
    <>
      <Field className={SETTINGS_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle className="flex items-center gap-2">
            <Bot className="size-4" />
            {formatProfileLabel(profile)}
          </FieldTitle>
          <FieldDescription>
            key {profile.has_api_key ? profile.api_key_masked : "not set"}
          </FieldDescription>
        </FieldContent>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!writeEnabled || mutations.testProfile.isPending}
            onClick={() =>
              mutations.testProfile.mutate(profile.id, {
                onSuccess: (result) => {
                  const payload = result as {
                    ok?: boolean;
                    error?: string;
                    output_preview?: string;
                  };
                  toast.success(payload.ok ? "Provider connection OK" : "Provider test failed", {
                    description: payload.error ?? payload.output_preview ?? undefined,
                  });
                },
                onError: (error) =>
                  toast.error("Provider test failed", { description: errorMessage(error) }),
              })
            }
          >
            Test
          </Button>
          <ProviderProfileEditDialog
            profile={profile}
            writeEnabled={writeEnabled}
            mutations={mutations}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={!writeEnabled || mutations.removeProfile.isPending}
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 className="size-4" />
            Delete
          </Button>
        </div>
      </Field>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete provider profile?</DialogTitle>
            <DialogDescription>
              This removes <span className="font-medium text-foreground">{profile.name}</span> and
              any role bindings that reference it. Runtime env values are not rolled back
              automatically.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={mutations.removeProfile.isPending}
              onClick={handleDelete}
            >
              Delete profile
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
