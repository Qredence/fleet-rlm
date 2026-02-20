import { useState } from "react";
import { toast } from "sonner";
import { SettingsToggleRow } from "./SettingsToggleRow";

// ── Notifications settings pane ─────────────────────────────────────
export function NotificationsPane() {
  const [skillComplete, setSkillComplete] = useState(true);
  const [validationAlerts, setValidationAlerts] = useState(true);
  const [weeklyDigest, setWeeklyDigest] = useState(false);

  return (
    <div>
      <SettingsToggleRow
        label="Skill completion"
        description="Notify when a skill finishes generation and validation."
        checked={skillComplete}
        onChange={(val) => {
          setSkillComplete(val);
          toast.success(
            val
              ? "Skill completion notifications on"
              : "Skill completion notifications off",
          );
        }}
      />
      <SettingsToggleRow
        label="Validation alerts"
        description="Alert when validation issues are detected."
        checked={validationAlerts}
        onChange={(val) => {
          setValidationAlerts(val);
          toast.success(val ? "Validation alerts on" : "Validation alerts off");
        }}
      />
      <SettingsToggleRow
        label="Weekly digest"
        description="Receive a weekly summary of skill usage and quality metrics."
        checked={weeklyDigest}
        onChange={(val) => {
          setWeeklyDigest(val);
          toast.success(
            val ? "Weekly digest enabled" : "Weekly digest disabled",
          );
        }}
      />
    </div>
  );
}
