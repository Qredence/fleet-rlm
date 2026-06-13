import { useMemo } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import { Skeleton } from "@/components/ui/skeleton";
import type { SettingsSection } from "@/features/settings/settings-content";
import { sectionDescriptions } from "@/features/settings/settings-content";
import {
  useLlmProfiles,
  useLlmProfilesMutations,
  useLlmRoleBindings,
} from "@/features/settings/use-llm-profiles";
import { useRuntimeSettings } from "@/features/settings/use-runtime-settings";

import { errorMessage } from "../runtime-status-panel";
import { ROLE_ROWS, SETTINGS_FIELD_CLASSNAME, SETTINGS_SECTION_CLASSNAME } from "./constants";
import { ActiveBindingsSummary } from "./active-bindings-summary";
import { ImportEnvButton } from "./import-env-button";
import { ProviderProfileForm } from "./provider-profile-form";
import { ProviderProfileList } from "./provider-profile-list";
import { RoleModelAssignment } from "./role-model-assignment";

interface RoleBinding {
  role: "planner" | "delegate" | "delegate_small";
  profile_id?: string | null;
  profile_name?: string | null;
  model_id?: string;
}

const EMPTY_BINDINGS: RoleBinding[] = [];

interface ProviderProfilesPanelProps {
  showAllSections: boolean;
  section?: SettingsSection;
}

export function ProviderProfilesPanel({ showAllSections, section }: ProviderProfilesPanelProps) {
  const { statusQuery } = useRuntimeSettings();
  const profilesQuery = useLlmProfiles();
  const rolesQuery = useLlmRoleBindings();
  const mutations = useLlmProfilesMutations();

  const writeEnabled = statusQuery.data?.write_enabled !== false;
  const showSection = (key: SettingsSection) => showAllSections || section === key;

  const profiles = profilesQuery.data ?? [];
  const bindings = rolesQuery.data?.bindings ?? EMPTY_BINDINGS;

  const bindingByRole = useMemo(() => {
    return Object.fromEntries(bindings.map((binding) => [binding.role, binding]));
  }, [bindings]);

  if (!showSection("litellm")) return null;

  const isLoading = profilesQuery.isPending || rolesQuery.isPending;
  const loadError = profilesQuery.error ?? rolesQuery.error;

  return (
    <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
      <div className="flex flex-col gap-1">
        <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
          {showAllSections ? "LLM Providers" : "Model routing"}
        </FieldLegend>
        <FieldDescription>{sectionDescriptions.litellm}</FieldDescription>
      </div>

      <FieldGroup className="gap-4">
        {!writeEnabled ? (
          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Write Protection</FieldTitle>
              <FieldDescription>
                Provider profile updates are disabled because APP_ENV is not local.
              </FieldDescription>
            </FieldContent>
            <Badge className="self-start" variant="destructive">
              Read-only
            </Badge>
          </Field>
        ) : null}

        {loadError ? (
          <Alert variant="destructive">
            <AlertTitle>Failed to load LLM settings</AlertTitle>
            <AlertDescription>{errorMessage(loadError)}</AlertDescription>
          </Alert>
        ) : null}

        {isLoading ? (
          <div className="flex flex-col gap-3 py-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <>
            <ActiveBindingsSummary bindings={bindings} />

            <SectionCard variant="subtle">
              <SectionCardHeader className="border-b border-border-subtle/70 pb-4">
                <SectionCardTitle>Role model assignment</SectionCardTitle>
                <SectionCardDescription>
                  Bind each runtime role to a provider profile and model. Changes apply to the
                  active runtime after save.
                </SectionCardDescription>
              </SectionCardHeader>
              <SectionCardContent className="flex flex-col gap-0 pt-2">
                {ROLE_ROWS.map((row) => (
                  <RoleModelAssignment
                    key={row.role}
                    role={row.role}
                    title={row.title}
                    description={row.description}
                    profiles={profiles}
                    binding={bindingByRole[row.role]}
                    writeEnabled={writeEnabled}
                    mutations={mutations}
                  />
                ))}
              </SectionCardContent>
            </SectionCard>

            <ImportEnvButton writeEnabled={writeEnabled} mutations={mutations} />

            <ProviderProfileList
              profiles={profiles}
              writeEnabled={writeEnabled}
              mutations={mutations}
            />

            <SectionCard variant="subtle">
              <SectionCardHeader>
                <SectionCardTitle>Add provider profile</SectionCardTitle>
                <SectionCardDescription>
                  Store provider credentials once, then assign models per runtime role above.
                </SectionCardDescription>
              </SectionCardHeader>
              <SectionCardContent className="pt-2">
                <ProviderProfileForm writeEnabled={writeEnabled} mutations={mutations} />
              </SectionCardContent>
            </SectionCard>
          </>
        )}
      </FieldGroup>
    </FieldSet>
  );
}
