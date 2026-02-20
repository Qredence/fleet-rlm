import { SettingsRow } from "../../shared/SettingsRow";

// ── About settings pane ─────────────────────────────────────────────
export function AboutPane() {
  return (
    <div>
      <SettingsRow label="Version">
        <span data-slot="settings-row-value" className="text-muted-foreground">
          Skill Fleet v0.9.0-beta
        </span>
      </SettingsRow>
      <SettingsRow label="Platform">
        <span data-slot="settings-row-value" className="text-muted-foreground">
          Qredence Agentic Fleet Ecosystem
        </span>
      </SettingsRow>
      <SettingsRow label="Build">
        <span data-slot="settings-row-value" className="text-muted-foreground">
          DSPy-based skill creation with HITL checkpoints
        </span>
      </SettingsRow>
      <SettingsRow label="Documentation" noBorder>
        <span data-slot="settings-row-value" className="text-muted-foreground">
          docs.qredence.ai
        </span>
      </SettingsRow>
    </div>
  );
}
