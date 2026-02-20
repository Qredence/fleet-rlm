import { SettingsRow } from "../../shared/SettingsRow";
import { ToggleSwitch } from "../../shared/ToggleSwitch";

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
      <ToggleSwitch checked={checked} onChange={onChange} />
    </SettingsRow>
  );
}
