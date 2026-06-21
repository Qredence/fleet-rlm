import { Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectPositioner,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { GEPAModuleInfo } from "@/lib/rlm-api";

import { CompactField } from "./form-field";
import type {
  OptimizationRunFormState,
  OptimizationTargetMode,
  SkillTargetMode,
} from "../optimization-model";

export function TargetSection({
  form,
  updateForm,
  modules,
  modulesLoading,
  selectedModule,
  isSubmitting,
}: {
  form: OptimizationRunFormState;
  updateForm: <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => void;
  modules: GEPAModuleInfo[];
  modulesLoading: boolean;
  selectedModule: GEPAModuleInfo | undefined;
  isSubmitting: boolean;
}) {
  return (
    <>
      <CompactField label="Target Mode" icon={<Sparkles className="size-4 text-primary" />}>
        <ToggleGroup
          value={form.targetMode}
          onValueChange={(value) => {
            if (value) updateForm("targetMode", value as OptimizationTargetMode);
          }}
          variant="outline"
          className="w-full flex"
          disabled={isSubmitting}
        >
          <ToggleGroupItem
            value="module"
            className="flex-1 font-medium transition-colors"
            disabled={isSubmitting}
          >
            Registered module
          </ToggleGroupItem>
          <ToggleGroupItem
            value="skill"
            className="flex-1 font-medium transition-colors"
            disabled={isSubmitting}
          >
            Skill file
          </ToggleGroupItem>
        </ToggleGroup>
      </CompactField>

      {form.targetMode === "module" ? (
        <CompactField label="Registered Module">
          <Select
            value={form.moduleSlug}
            onValueChange={(value) => value && updateForm("moduleSlug", value)}
            disabled={isSubmitting || modulesLoading || modules.length === 0}
          >
            <SelectTrigger className="w-full">
              <SelectValue>{selectedModule?.label ?? (form.moduleSlug || "Select module")}</SelectValue>
            </SelectTrigger>
            <SelectPositioner align="start">
              <SelectContent className="border-border">
                <SelectGroup>
                  {modules.map((module) => (
                    <SelectItem key={module.slug} value={module.slug}>
                      {module.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </SelectPositioner>
          </Select>
          {selectedModule ? (
            <div className="rounded-lg border border-border-subtle bg-muted/10 px-4 py-3 text-xs text-muted-foreground leading-normal transition-all duration-200">
              <div className="flex flex-wrap gap-1.5 mb-2">
                <Badge variant="outline" className="border-border-subtle bg-background">
                  {selectedModule.optimization_target_kind ?? "custom"}
                </Badge>
                {selectedModule.signature_class_name ? (
                  <Badge variant="secondary" className="bg-muted text-muted-foreground">
                    {selectedModule.signature_class_name}
                  </Badge>
                ) : null}
                {selectedModule.runtime_module_name ? (
                  <Badge variant="secondary" className="bg-muted text-muted-foreground">
                    {selectedModule.runtime_module_name}
                  </Badge>
                ) : null}
              </div>
              {selectedModule.description ? (
                <div className="mt-1">{selectedModule.description}</div>
              ) : null}
            </div>
          ) : null}
        </CompactField>
      ) : (
        <>
          <CompactField label="Skill Target Sub-mode">
            <ToggleGroup
              value={form.skillTargetMode}
              onValueChange={(value) => {
                if (value) updateForm("skillTargetMode", value as SkillTargetMode);
              }}
              variant="outline"
              className="w-full flex"
              disabled={isSubmitting}
            >
              <ToggleGroupItem
                value="name"
                className="flex-1 font-medium transition-colors"
                disabled={isSubmitting}
              >
                Bundled name
              </ToggleGroupItem>
              <ToggleGroupItem
                value="path"
                className="flex-1 font-medium transition-colors"
                disabled={isSubmitting}
              >
                Skill path
              </ToggleGroupItem>
            </ToggleGroup>
          </CompactField>
          {form.skillTargetMode === "name" ? (
            <CompactField label="Skill Name">
              <Input
                value={form.skillName}
                onChange={(event) => updateForm("skillName", event.target.value)}
                placeholder="optimization"
                disabled={isSubmitting}
              />
            </CompactField>
          ) : (
            <CompactField label="Skill Path">
              <Input
                value={form.skillPath}
                onChange={(event) => updateForm("skillPath", event.target.value)}
                placeholder="skills/custom/SKILL.md"
                disabled={isSubmitting}
              />
            </CompactField>
          )}
        </>
      )}
    </>
  );
}
