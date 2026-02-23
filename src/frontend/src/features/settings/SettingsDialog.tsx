import { Drawer } from "vaul";
import { X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { typo } from "@/lib/config/typo";
import { useNavigation } from "@/hooks/useNavigation";
import { useIsMobile } from "@/components/ui/use-mobile";
import { IconButton } from "@/components/ui/icon-button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SettingsPaneContent } from "@/features/settings/SettingsPaneContent";

// ── Shared settings body (mobile vs desktop layout) ─────────────────
function SettingsBody({
  isDark,
  onToggleTheme,
  isMobile,
}: {
  isDark: boolean;
  onToggleTheme: () => void;
  isMobile: boolean;
}) {
  /* ── Shared grouped layout (mobile + desktop) ─────────────────── */
  return (
    <div className="flex flex-col h-[85dvh] sm:h-[520px]">
      <div className="shrink-0 border-b border-border-subtle">
        <div className={isMobile ? "px-4 pt-3 pb-3" : "px-6 pt-5 pb-3"}>
          <span className="text-foreground" style={typo.h4}>
            General
          </span>
          <p className="mt-1 text-sm text-muted-foreground">
            Functional settings only for v0.4.8: theme, anonymous telemetry, and
            LiteLLM runtime integration.
          </p>
        </div>
      </div>
      <ScrollArea className="flex-1">
        <div className={isMobile ? "px-4" : "px-6"}>
          <SettingsPaneContent isDark={isDark} onToggleTheme={onToggleTheme} />
        </div>
      </ScrollArea>
    </div>
  );
}

// ── Main settings dialog ────────────────────────────────────────────
interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * SettingsDialog — theme and preference configuration.
 *
 * Consumes `isDark` / `toggleTheme` from NavigationContext.
 * Only `open` / `onOpenChange` remain as props (dialog-local state
 * owned by the parent header).
 */
export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  const { isDark, toggleTheme } = useNavigation();
  const isMobile = useIsMobile();

  /* ── Mobile: iOS 26 full-screen Liquid Glass sheet ─────────────── */
  if (isMobile) {
    return (
      <Drawer.Root open={open} onOpenChange={onOpenChange}>
        <Drawer.Portal>
          <Drawer.Overlay
            className="fixed inset-0 z-50"
            style={{ backgroundColor: "var(--glass-overlay)" }}
          />
          <Drawer.Content
            className="fixed inset-x-0 bottom-0 z-50 flex flex-col outline-none"
            style={{
              height: "95dvh",
              borderTopLeftRadius: "var(--radius-card)",
              borderTopRightRadius: "var(--radius-card)",
              backgroundColor: "var(--glass-sheet-bg)",
              backdropFilter: "blur(var(--glass-sheet-blur))",
              WebkitBackdropFilter: "blur(var(--glass-sheet-blur))",
              borderTop: "0.5px solid var(--glass-sheet-border)",
            }}
          >
            {/* iOS 26 grab handle */}
            <div className="flex items-center justify-center py-2 shrink-0">
              <div
                className="w-9 h-[5px] rounded-full"
                style={{ backgroundColor: "var(--glass-sheet-handle)" }}
                aria-hidden="true"
              />
            </div>

            {/* Sheet header with close button */}
            <div className="flex items-center justify-between px-4 pb-3 shrink-0">
              <Drawer.Title>
                <span className="text-foreground" style={typo.h3}>
                  Settings
                </span>
              </Drawer.Title>
              <IconButton
                onClick={() => onOpenChange(false)}
                aria-label="Close settings"
                className="touch-target"
              >
                <X className="size-5 text-muted-foreground" />
              </IconButton>
            </div>
            <Drawer.Description className="sr-only">
              Configure Skill Fleet preferences and appearance
            </Drawer.Description>

            {/* Settings body */}
            <div className="flex-1 min-h-0">
              <SettingsBody
                isDark={isDark}
                onToggleTheme={toggleTheme}
                isMobile
              />
            </div>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    );
  }

  /* ── Desktop: standard dialog ──────────────────────────────────── */
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px] p-0 gap-0 overflow-hidden rounded-card">
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <DialogDescription className="sr-only">
          Configure Skill Fleet preferences and appearance
        </DialogDescription>

        <SettingsBody
          isDark={isDark}
          onToggleTheme={toggleTheme}
          isMobile={false}
        />
      </DialogContent>
    </Dialog>
  );
}
