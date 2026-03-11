import { SettingsRow } from "@/features/settings/SettingsRow";
import { Switch } from "@/components/ui/switch";

// ── Toggle row ──────────────────────────────────────────────────────
interface SettingsToggleRowProps {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (val: boolean) => void;
}

export function SettingsToggleRow({
  label,
  description,
  checked,
  onChange,
}: SettingsToggleRowProps) {
  return (
    <SettingsRow label={label} description={description}>
      <Switch checked={checked} onCheckedChange={onChange} />
    </SettingsRow>
  );
}
