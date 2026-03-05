import { SlidersHorizontal } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { IconButton } from "@/components/ui/icon-button";
import { Switch } from "@/components/ui/switch";

const settingRows = [
  {
    key: "modelOverride",
    label: "Per-message model override",
    description: "Requires backend routing contract support",
  },
  {
    key: "fileUpload",
    label: "File upload to backend",
    description: "Upload transport endpoint is not available yet",
  },
] as const;

function SettingsDropdown() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <span className="inline-flex">
          <IconButton
            type="button"
            aria-label="Composer settings"
            className="touch-target rounded-full"
          >
            <SlidersHorizontal className="size-5 text-foreground" />
          </IconButton>
        </span>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="start"
        className="w-72 border-border bg-popover"
      >
        <DropdownMenuLabel className="text-xs text-muted-foreground">
          Chat input settings
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        <div className="space-y-3 p-2">
          {settingRows.map((setting) => (
            <div
              key={setting.key}
              className="flex items-start justify-between gap-3 rounded-lg px-2 py-1"
            >
              <div className="space-y-0.5">
                <p className="text-xs font-medium text-foreground">
                  {setting.label}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {setting.description}
                </p>
              </div>
              <Switch checked={false} disabled aria-label={setting.label} />
            </div>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export { SettingsDropdown };
