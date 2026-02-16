export type TraceMode = "compact" | "verbose" | "off";

export type TranscriptRole =
  | "system"
  | "user"
  | "assistant"
  | "tool"
  | "status"
  | "error";

export type WorkingPhase = "idle" | "thinking" | "tool";

export type OverlayView =
  | "none"
  | "palette-root"
  | "palette-settings"
  | "settings-edit"
  | "status-panel";

export interface TranscriptLine {
  id: string;
  role: TranscriptRole;
  text: string;
}

export interface MentionItem {
  path: string;
  kind: string;
  score: number;
}

export interface CliOptions {
  pythonBin: string;
  traceMode: TraceMode;
  docsPath?: string;
  volumeName?: string;
  secretName?: string;
  hydraOverrides: string[];
}

export interface BridgeSessionInit {
  session_id?: string;
  commands?: {
    tool_commands?: string[];
    wrapper_commands?: string[];
  };
}

export interface SettingsSnapshot {
  values: Record<string, string>;
  masked_values: Record<string, string>;
  secret_values_included?: boolean;
}
