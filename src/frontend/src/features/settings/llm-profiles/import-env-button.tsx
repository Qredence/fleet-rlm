import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldTitle } from "@/components/ui/field";
import type { useLlmProfilesMutations } from "@/features/settings/use-llm-profiles";

import { SETTINGS_FIELD_CLASSNAME, errorMessage } from "./constants";

interface ImportEnvButtonProps {
  writeEnabled: boolean;
  mutations: ReturnType<typeof useLlmProfilesMutations>;
}

export function ImportEnvButton({ writeEnabled, mutations }: ImportEnvButtonProps) {
  const handleImportEnv = () => {
    mutations.importFromEnv.mutate(undefined, {
      onSuccess: () => toast.success("Imported provider profile from .env"),
      onError: (error) => toast.error("Import failed", { description: errorMessage(error) }),
    });
  };

  return (
    <Field className={SETTINGS_FIELD_CLASSNAME}>
      <FieldContent>
        <FieldTitle>Import from .env</FieldTitle>
        <FieldDescription>
          Create a provider profile from the current DSPY_* environment variables.
        </FieldDescription>
      </FieldContent>
      <Button
        variant="outline"
        className="self-start rounded-lg"
        disabled={!writeEnabled || mutations.importFromEnv.isPending}
        onClick={handleImportEnv}
      >
        Import from .env
      </Button>
    </Field>
  );
}
