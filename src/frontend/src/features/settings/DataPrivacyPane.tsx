import { useState } from "react";
import { toast } from "sonner";
import { typo } from "@/lib/config/typo";
import { Button } from "@/components/ui/button";
import { SettingsRow } from "@/components/shared/SettingsRow";
import { SettingsToggleRow } from "@/features/settings/SettingsToggleRow";

// ── Data & Privacy settings pane ────────────────────────────────────
export function DataPrivacyPane() {
  const [telemetry, setTelemetry] = useState(true);

  return (
    <div>
      <SettingsToggleRow
        label="Usage telemetry"
        description="Share anonymized usage data to help improve Skill Fleet."
        checked={telemetry}
        onChange={(val) => {
          setTelemetry(val);
          toast.success(val ? "Telemetry enabled" : "Telemetry disabled");
        }}
      />
      <SettingsRow
        label="Clear local data"
        description="Remove all locally cached skills and preferences."
      >
        <Button
          variant="destructive-ghost"
          className="rounded-lg shrink-0"
          style={typo.label}
          onClick={() => {
            toast.success("Local data cleared", {
              description:
                "All cached skills and preferences have been removed.",
            });
          }}
        >
          Clear data
        </Button>
      </SettingsRow>
    </div>
  );
}
