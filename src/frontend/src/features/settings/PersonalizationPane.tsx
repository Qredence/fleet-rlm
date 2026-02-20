import { useState } from "react";
import { toast } from "sonner";
import { SettingsSelectField } from "@/features/settings/SettingsSelectField";
import { SettingsToggleRow } from "@/features/settings/SettingsToggleRow";

// ── Personalization settings pane ───────────────────────────────────
export function PersonalizationPane() {
  const [compactMode, setCompactMode] = useState(false);
  const [animationsEnabled, setAnimationsEnabled] = useState(true);

  return (
    <div>
      <SettingsToggleRow
        label="Compact mode"
        description="Reduce spacing and padding for denser layouts."
        checked={compactMode}
        onChange={(val) => {
          setCompactMode(val);
          toast.success(val ? "Compact mode enabled" : "Compact mode disabled");
        }}
      />
      <SettingsToggleRow
        label="Animations"
        description="Enable motion and transition animations throughout the UI."
        checked={animationsEnabled}
        onChange={(val) => {
          setAnimationsEnabled(val);
          toast.success(val ? "Animations enabled" : "Animations disabled");
        }}
      />
      <SettingsSelectField
        label="Default view"
        value="Cards"
        options={["Cards", "List", "Compact list"]}
        onChange={(val) => {
          toast.success("Default view updated", { description: val });
        }}
        description="Default display format for the skill library."
      />
    </div>
  );
}
