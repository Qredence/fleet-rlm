import { useEffect, useMemo, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { toast } from "sonner";

import { SettingsRow } from "@/components/shared/SettingsRow";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/components/ui/utils";
import { SettingsToggleRow } from "@/features/settings/SettingsToggleRow";
import type { SettingsSection } from "@/features/settings/types";
import {
  computeLmRuntimeUpdates,
  useRuntimeSettings,
} from "@/features/settings/useRuntimeSettings";
import { telemetryClient } from "@/lib/telemetry/client";

interface GroupedSettingsPaneProps {
  isDark: boolean;
  onToggleTheme: () => void;
  section?: SettingsSection;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

export function GroupedSettingsPane({
  isDark,
  onToggleTheme,
  section,
}: GroupedSettingsPaneProps) {
  const { settingsQuery, statusQuery, saveSettings } = useRuntimeSettings();

  const [telemetryEnabled, setTelemetryEnabled] = useState(true);
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [lmModel, setLmModel] = useState("");
  const [delegateLmModel, setDelegateLmModel] = useState("");
  const [delegateLmSmallModel, setDelegateLmSmallModel] = useState("");
  const [baselineApiBase, setBaselineApiBase] = useState("");
  const [baselineApiKey, setBaselineApiKey] = useState("");
  const [baselineLmModel, setBaselineLmModel] = useState("");
  const [baselineDelegateLmModel, setBaselineDelegateLmModel] = useState("");
  const [baselineDelegateLmSmallModel, setBaselineDelegateLmSmallModel] =
    useState("");

  useEffect(() => {
    setTelemetryEnabled(telemetryClient.isAnonymousTelemetryEnabled());
  }, []);

  useEffect(() => {
    const values = settingsQuery.data?.values;
    if (!values) return;
    const nextLmModel = values.DSPY_LM_MODEL ?? "";
    const nextDelegateLmModel = values.DSPY_DELEGATE_LM_MODEL ?? "";
    const nextDelegateLmSmallModel = values.DSPY_DELEGATE_LM_SMALL_MODEL ?? "";
    const nextApiBase = values.DSPY_LM_API_BASE ?? "";
    const nextApiKey = values.DSPY_LLM_API_KEY ?? "";
    setLmModel(nextLmModel);
    setDelegateLmModel(nextDelegateLmModel);
    setDelegateLmSmallModel(nextDelegateLmSmallModel);
    setApiBase(nextApiBase);
    setApiKey(nextApiKey);
    setBaselineLmModel(nextLmModel);
    setBaselineDelegateLmModel(nextDelegateLmModel);
    setBaselineDelegateLmSmallModel(nextDelegateLmSmallModel);
    setBaselineApiBase(nextApiBase);
    setBaselineApiKey(nextApiKey);
  }, [settingsQuery.data]);

  const runtimeUpdates = useMemo(() => {
    return computeLmRuntimeUpdates(
      {
        DSPY_LM_MODEL: lmModel,
        DSPY_DELEGATE_LM_MODEL: delegateLmModel,
        DSPY_DELEGATE_LM_SMALL_MODEL: delegateLmSmallModel,
        DSPY_LM_API_BASE: apiBase,
        DSPY_LLM_API_KEY: apiKey,
      },
      {
        DSPY_LM_MODEL: baselineLmModel,
        DSPY_DELEGATE_LM_MODEL: baselineDelegateLmModel,
        DSPY_DELEGATE_LM_SMALL_MODEL: baselineDelegateLmSmallModel,
        DSPY_LM_API_BASE: baselineApiBase,
        DSPY_LLM_API_KEY: baselineApiKey,
      },
    );
  }, [
    apiBase,
    apiKey,
    baselineApiBase,
    baselineApiKey,
    baselineDelegateLmModel,
    baselineDelegateLmSmallModel,
    baselineLmModel,
    delegateLmModel,
    delegateLmSmallModel,
    lmModel,
  ]);

  const dirtyKeys = useMemo(
    () => Object.keys(runtimeUpdates),
    [runtimeUpdates],
  );
  const status = statusQuery.data;
  const writeEnabled = status?.write_enabled !== false;

  const handleSaveLmSettings = () => {
    if (!writeEnabled) {
      toast.error("Runtime settings are read-only in this environment");
      return;
    }
    if (dirtyKeys.length === 0) {
      toast("No LM integration changes to save");
      return;
    }
    saveSettings.mutate(runtimeUpdates, {
      onSuccess: (result) => {
        const updated = result.updated ?? [];
        toast.success("LM integration settings saved", {
          description:
            updated.length > 0
              ? `Updated: ${updated.join(", ")}`
              : "No keys changed.",
        });
      },
      onError: (error) => {
        toast.error("Failed to save LM integration settings", {
          description: errorMessage(error),
        });
      },
    });
  };

  const saveDisabled =
    dirtyKeys.length === 0 || saveSettings.isPending || !writeEnabled;
  const showAllSections = section == null;
  const showSection = (key: SettingsSection) =>
    showAllSections || section === key;

  return (
    <div>
      {showSection("appearance") && (
        <>
          {showAllSections && (
            <div className="py-3 border-b border-border-subtle">
              <span className="text-sm text-muted-foreground font-medium">
                Appearance
              </span>
            </div>
          )}

          <SettingsRow
            label="Theme"
            description="Choose the interface appearance for the web app."
            noBorder={section === "appearance"}
          >
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
        </>
      )}

      {showSection("telemetry") && (
        <>
          {showAllSections && (
            <div className="py-3 border-b border-border-subtle">
              <span className="text-sm text-muted-foreground font-medium">
                Telemetry
              </span>
            </div>
          )}

          <SettingsToggleRow
            label="Anonymous telemetry"
            description="Share anonymous usage telemetry to help improve Fleet-RLM. This preference now updates web PostHog capture immediately and propagates to backend AI analytics for new chat turns."
            checked={telemetryEnabled}
            onChange={(val) => {
              setTelemetryEnabled(val);
              telemetryClient.setAnonymousTelemetryEnabled(val);
              telemetryClient.capture("telemetry_preference_updated", {
                enabled: val,
                scope: "anonymous_only_web",
                source: "grouped_settings",
              });
              toast.success(
                val
                  ? "Anonymous telemetry enabled"
                  : "Anonymous telemetry disabled",
              );
            }}
          />

          <SettingsRow
            label="Telemetry scope"
            description="No account/billing/profile settings are exposed here in v0.4.8. This surface is intentionally limited to functional runtime and privacy controls."
            noBorder={section === "telemetry"}
          >
            <span className="text-xs text-muted-foreground">Anonymous-only</span>
          </SettingsRow>
        </>
      )}

      {showSection("litellm") && (
        <>
          {showAllSections && (
            <div className="py-3 border-b border-border-subtle">
              <span className="text-sm text-muted-foreground font-medium">
                LiteLLM Integration
              </span>
            </div>
          )}

          <SettingsRow
            label="LiteLLM integration"
            description="Configure a custom LiteLLM-compatible endpoint and API key for planner/provider routing. These values are saved through the runtime settings API when local writes are enabled."
          />

          {!writeEnabled && (
            <SettingsRow
              label="Write Protection"
              description="Runtime settings updates are disabled because APP_ENV is not local."
            >
              <span className="text-xs text-muted-foreground">Read-only</span>
            </SettingsRow>
          )}

          <SettingsRow
            label="Planner LM model"
            description="Primary planner model identifier used for chat turns and planning."
          >
            <Input
              type="text"
              value={lmModel}
              placeholder="openai/gpt-4o-mini"
              autoComplete="off"
              onChange={(event) => setLmModel(event.target.value)}
              className="w-[260px] max-w-[50vw]"
            />
          </SettingsRow>

          <SettingsRow
            label="Delegate LM model"
            description="Optional delegate model used for recursive or long-context sub-agent tasks."
          >
            <Input
              type="text"
              value={delegateLmModel}
              placeholder="openai/gpt-4.1-mini"
              autoComplete="off"
              onChange={(event) => setDelegateLmModel(event.target.value)}
              className="w-[260px] max-w-[50vw]"
            />
          </SettingsRow>

          <SettingsRow
            label="Delegate small LM model"
            description="Optional lightweight delegate model for fast/low-cost operations."
          >
            <Input
              type="text"
              value={delegateLmSmallModel}
              placeholder="openai/gpt-4o-mini"
              autoComplete="off"
              onChange={(event) => setDelegateLmSmallModel(event.target.value)}
              className="w-[260px] max-w-[50vw]"
            />
          </SettingsRow>

          <SettingsRow
            label="Custom API endpoint"
            description="Optional LiteLLM (or provider proxy) base URL."
          >
            <Input
              type="text"
              value={apiBase}
              placeholder="https://your-litellm.example.com/v1"
              autoComplete="off"
              onChange={(event) => setApiBase(event.target.value)}
              className="w-[260px] max-w-[50vw]"
            />
          </SettingsRow>

          <SettingsRow
            label="API key"
            description="Provider or proxy key used for LM requests. Leave unchanged to keep the current value."
          >
            <Input
              type="password"
              value={apiKey}
              placeholder="sk-..."
              autoComplete="off"
              onChange={(event) => setApiKey(event.target.value)}
              className="w-[260px] max-w-[50vw]"
            />
          </SettingsRow>

          <SettingsRow
            label="Save LM integration settings"
            description={
              status
                ? `Environment: ${status.app_env}. Saves via /api/v1/runtime/settings when local writes are enabled.`
                : "Saves via /api/v1/runtime/settings when local writes are enabled."
            }
            noBorder
          >
            <Button
              variant="secondary"
              className="rounded-lg"
              onClick={handleSaveLmSettings}
              disabled={saveDisabled}
            >
              {saveSettings.isPending ? "Saving…" : "Save settings"}
            </Button>
          </SettingsRow>
        </>
      )}
    </div>
  );
}
