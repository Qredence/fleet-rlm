import { useState } from "react";
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
import { categories, type Category } from "@/features/settings/types";
import { SettingsNavItem } from "@/components/shared/SettingsNavItem";
import { SettingsPaneContent } from "@/features/settings/SettingsPaneContent";

// ── Shared settings body (mobile vs desktop layout) ─────────────────
function SettingsBody({
  isDark,
  onToggleTheme,
  activeCategory,
  setActiveCategory,
  isMobile,
}: {
  isDark: boolean;
  onToggleTheme: () => void;
  activeCategory: Category;
  setActiveCategory: (cat: Category) => void;
  isMobile: boolean;
}) {
  const activeCategoryLabel =
    categories.find((c) => c.key === activeCategory)?.label ?? "";

  if (isMobile) {
    /* ── Mobile: vertical full-screen sheet layout ──────────────── */
    return (
      <div className="flex flex-col h-full">
        {/* Horizontal scrollable category tabs */}
        <div className="shrink-0 border-b border-border-subtle">
          <nav className="flex gap-0.5 px-3 py-2 overflow-x-auto no-scrollbar">
            {categories.map((cat) => (
              <SettingsNavItem
                key={cat.key}
                icon={cat.icon}
                label={cat.label}
                isActive={activeCategory === cat.key}
                onClick={() => setActiveCategory(cat.key)}
                isMobile
              />
            ))}
          </nav>
        </div>

        {/* Content */}
        <ScrollArea className="flex-1">
          <div className="px-4">
            <SettingsPaneContent
              activeCategory={activeCategory}
              isDark={isDark}
              onToggleTheme={onToggleTheme}
            />
          </div>
        </ScrollArea>
      </div>
    );
  }

  /* ── Desktop: sidebar + content layout ───────────────────────── */
  return (
    <div className="flex flex-col sm:flex-row h-[85dvh] sm:h-[520px]">
      {/* Left sidebar */}
      <div className="sm:w-[200px] shrink-0 border-b sm:border-b-0 sm:border-r border-border-subtle bg-secondary/30 flex flex-col">
        <div className="px-4 pt-5 pb-3">
          <span className="text-foreground" style={typo.h4}>
            Settings
          </span>
        </div>
        <nav className="flex sm:flex-col gap-0.5 px-2 pb-2 sm:pb-0 sm:flex-1 overflow-x-auto sm:overflow-x-visible no-scrollbar">
          {categories.map((cat) => (
            <SettingsNavItem
              key={cat.key}
              icon={cat.icon}
              label={cat.label}
              isActive={activeCategory === cat.key}
              onClick={() => setActiveCategory(cat.key)}
            />
          ))}
        </nav>
      </div>

      {/* Right content */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-6 pt-5 pb-3 border-b border-border-subtle">
          <span className="text-foreground" style={typo.h4}>
            {activeCategoryLabel}
          </span>
        </div>
        <ScrollArea className="flex-1">
          <div className="px-6">
            <SettingsPaneContent
              activeCategory={activeCategory}
              isDark={isDark}
              onToggleTheme={onToggleTheme}
            />
          </div>
        </ScrollArea>
      </div>
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
  const [activeCategory, setActiveCategory] = useState<Category>("general");

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
                activeCategory={activeCategory}
                setActiveCategory={setActiveCategory}
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
          activeCategory={activeCategory}
          setActiveCategory={setActiveCategory}
          isMobile={false}
        />
      </DialogContent>
    </Dialog>
  );
}
