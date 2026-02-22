import { SettingsRow } from "@/components/shared/SettingsRow";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// ── Select dropdown row ─────────────────────────────────────────────
interface SettingsSelectFieldProps {
  label: string;
  value: string;
  options: string[];
  onChange: (val: string) => void;
  description?: string;
}

export function SettingsSelectField({
  label,
  value,
  options,
  onChange,
  description,
}: SettingsSelectFieldProps) {
  return (
    <SettingsRow label={label} description={description}>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="w-auto min-w-[120px] border-0 bg-transparent shadow-none h-auto py-1">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt} value={opt}>
              {opt}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </SettingsRow>
  );
}
