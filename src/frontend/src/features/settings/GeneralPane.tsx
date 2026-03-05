import { useState } from "react";
import { Sun, Moon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";
import { SettingsRow } from "@/components/shared/SettingsRow";
import { SettingsSelectField } from "@/features/settings/SettingsSelectField";
import { SettingsToggleRow } from "@/features/settings/SettingsToggleRow";

// ── General settings pane ───────────────────────────────────────────
interface GeneralPaneProps {
  isDark: boolean;
  onToggleTheme: () => void;
}

export function GeneralPane({ isDark, onToggleTheme }: GeneralPaneProps) {
  const [language, setLanguage] = useState("Auto-detect");
  const [autoSave, setAutoSave] = useState(true);

  return (
    <div>
      {/* Appearance */}
      <SettingsRow label="Appearance">
        <div className="flex items-center gap-1 bg-secondary rounded-lg p-0.5">
          <Button
            variant="ghost"
            className={cn(
              "gap-1.5 px-3 py-1.5 h-auto rounded-md",
              !isDark && "bg-background shadow-sm",
            )}
            onClick={() => {
              if (isDark) {
                onToggleTheme();
                toast.success("Switched to Light mode");
              }
            }}
          >
            <Sun className="w-3.5 h-3.5" />
            Light
          </Button>
          <Button
            variant="ghost"
            className={cn(
              "gap-1.5 px-3 py-1.5 h-auto rounded-md",
              isDark && "bg-background shadow-sm",
            )}
            onClick={() => {
              if (!isDark) {
                onToggleTheme();
                toast.success("Switched to Dark mode");
              }
            }}
          >
            <Moon className="w-3.5 h-3.5" />
            Dark
          </Button>
        </div>
      </SettingsRow>

      <SettingsSelectField
        label="Language"
        value={language}
        options={[
          "Auto-detect",
          "English",
          "Spanish",
          "French",
          "German",
          "Japanese",
        ]}
        onChange={(val) => {
          setLanguage(val);
          toast.success("Language updated", { description: val });
        }}
      />

      <SettingsToggleRow
        label="Auto-save drafts"
        description="Automatically save skill drafts while editing."
        checked={autoSave}
        onChange={(val) => {
          setAutoSave(val);
          toast.success(val ? "Auto-save enabled" : "Auto-save disabled");
        }}
      />

      <SettingsSelectField
        label="Default quality threshold"
        value="90%"
        options={["70%", "80%", "85%", "90%", "95%"]}
        onChange={(val) => {
          toast.success("Quality threshold updated", { description: val });
        }}
        description="Minimum quality score for auto-validation."
      />
    </div>
  );
}
