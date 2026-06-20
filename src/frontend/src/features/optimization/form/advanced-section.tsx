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

import { CompactField } from "./form-field";
import type { OptimizationRunFormState } from "../optimization-model";

export function AdvancedSection({
  form,
  updateForm,
  isSubmitting,
}: {
  form: OptimizationRunFormState;
  updateForm: <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => void;
  isSubmitting: boolean;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-4">
      <CompactField label="Auto">
        <Select
          value={form.auto}
          onValueChange={(value) => updateForm("auto", value as OptimizationRunFormState["auto"])}
          disabled={isSubmitting}
        >
          <SelectTrigger className="h-10 w-full">
            <SelectValue>{form.auto}</SelectValue>
          </SelectTrigger>
          <SelectPositioner align="start">
            <SelectContent className="border-border">
              <SelectGroup>
                <SelectItem value="light">light</SelectItem>
                <SelectItem value="medium">medium</SelectItem>
                <SelectItem value="heavy">heavy</SelectItem>
              </SelectGroup>
            </SelectContent>
          </SelectPositioner>
        </Select>
      </CompactField>
      <CompactField label="Train Ratio">
        <Input
          value={form.trainRatio}
          onChange={(event) => updateForm("trainRatio", event.target.value)}
          inputMode="decimal"
          disabled={isSubmitting}
          className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary text-center font-mono"
        />
      </CompactField>
      <CompactField label="Max Calls">
        <Input
          value={form.maxMetricCalls}
          onChange={(event) => updateForm("maxMetricCalls", event.target.value)}
          inputMode="numeric"
          placeholder="auto"
          disabled={isSubmitting}
          className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary text-center font-mono"
        />
      </CompactField>
      <CompactField label="Output Path">
        <Input
          value={form.outputPath}
          onChange={(event) => updateForm("outputPath", event.target.value)}
          placeholder="optional"
          disabled={isSubmitting}
          className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary font-mono"
        />
      </CompactField>
    </div>
  );
}
