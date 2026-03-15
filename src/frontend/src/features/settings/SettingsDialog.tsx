import { useEffect, useState } from "react";
import { Drawer } from "vaul";
import { X } from "lucide-react";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { IconButton } from "@/components/ui/icon-button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
} from "@/components/ui/sidebar";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useIsMobile } from "@/hooks/useIsMobile";
import { GroupedSettingsPane } from "@/features/settings/GroupedSettingsPane";
import {
  sectionDescriptions,
  settingsSections,
  type SettingsSection,
} from "@/features/settings/types";
import { useThemeStore } from "@/stores/themeStore";
import { cn } from "@/lib/utils/cn";

interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialSection?: SettingsSection;
}

function resolveInitialSection(section: SettingsSection | undefined): SettingsSection {
  return section && settingsSections.some((candidate) => candidate.key === section)
    ? section
    : "appearance";
}

function SectionContent({
  isDark,
  onToggleTheme,
  activeSection,
  isMobile,
}: {
  isDark: boolean;
  onToggleTheme: () => void;
  activeSection: SettingsSection;
  isMobile: boolean;
}) {
  const activeMeta = settingsSections.find((section) => section.key === activeSection);

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col",
        isMobile ? "h-(--settings-dialog-section-height-mobile)" : "h-full",
      )}
    >
      <div className="shrink-0 border-b border-border-subtle/70">
        <div className={isMobile ? "px-4 pt-3 pb-3" : "px-6 pt-5 pb-3"}>
          {isMobile ? (
            <span className="text-foreground typo-h4">{activeMeta?.label}</span>
          ) : (
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem>
                  <span className="text-muted-foreground">Settings</span>
                </BreadcrumbItem>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  <BreadcrumbPage>{activeMeta?.label}</BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
          )}
          <p className="mt-1 text-sm text-muted-foreground">{sectionDescriptions[activeSection]}</p>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className={isMobile ? "px-4 pb-4" : "px-6 pb-6"}>
          <GroupedSettingsPane
            isDark={isDark}
            onToggleTheme={onToggleTheme}
            section={activeSection}
          />
        </div>
      </ScrollArea>
    </div>
  );
}

function MobileSectionNav({
  activeSection,
  onSelectSection,
}: {
  activeSection: SettingsSection;
  onSelectSection: (section: SettingsSection) => void;
}) {
  const handleValueChange = (value: string) => {
    if (value && settingsSections.some((section) => section.key === value)) {
      onSelectSection(value as SettingsSection);
    }
  };

  return (
    <div className="shrink-0 overflow-x-auto border-b border-border-subtle/70 px-4 py-2">
      <ToggleGroup
        type="single"
        value={activeSection}
        onValueChange={handleValueChange}
        variant="outline"
        size="sm"
        aria-label="Settings sections"
        className="w-max"
      >
        {settingsSections.map((section) => {
          const Icon = section.icon;
          return (
            <ToggleGroupItem
              key={section.key}
              value={section.key}
              aria-label={section.label}
              className="touch-target min-w-max px-3"
            >
              <Icon aria-hidden="true" />
              {section.label}
            </ToggleGroupItem>
          );
        })}
      </ToggleGroup>
    </div>
  );
}

/**
 * SettingsDialog — theme and preference configuration.
 *
 * Consumes `isDark` / `toggleTheme` from ThemeStore.
 * `open` / `onOpenChange` are required props; `initialSection` optionally
 * focuses a specific settings section when opening.
 */
export function SettingsDialog({ open, onOpenChange, initialSection }: SettingsDialogProps) {
  const { isDark, toggle: toggleTheme } = useThemeStore();
  const isMobile = useIsMobile();
  const [activeSection, setActiveSection] = useState<SettingsSection>(() =>
    resolveInitialSection(initialSection),
  );

  useEffect(() => {
    if (open) {
      setActiveSection(resolveInitialSection(initialSection));
    }
  }, [open, initialSection]);

  /* ── Mobile: iOS 26 full-screen Liquid Glass sheet ─────────────── */
  if (isMobile) {
    return (
      <Drawer.Root open={open} onOpenChange={onOpenChange}>
        <Drawer.Portal>
          <Drawer.Overlay className="surface-glass-overlay fixed inset-0 z-50" />
          <Drawer.Content className="surface-glass-sheet fixed inset-x-0 bottom-0 z-50 flex h-(--settings-dialog-height-mobile) flex-col outline-none">
            {/* iOS 26 grab handle */}
            <div className="flex items-center justify-center py-2 shrink-0">
              <div className="surface-glass-handle h-1.25 w-9 rounded-full" aria-hidden="true" />
            </div>

            {/* Sheet header with close button */}
            <div className="flex items-center justify-between px-4 pb-3 shrink-0">
              <Drawer.Title>
                <span className="text-foreground typo-h3">Settings</span>
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

            <MobileSectionNav activeSection={activeSection} onSelectSection={setActiveSection} />

            <div className="flex-1 min-h-0">
              <SectionContent
                isDark={isDark}
                onToggleTheme={toggleTheme}
                activeSection={activeSection}
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
      <DialogContent className="overflow-hidden border-border-subtle/70 bg-card/95 p-0 md:max-h-130 md:max-w-195 lg:max-w-215">
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <DialogDescription className="sr-only">
          Configure Skill Fleet preferences and appearance
        </DialogDescription>
        <SidebarProvider className="items-start">
          <Sidebar
            collapsible="none"
            className="hidden border-r border-border-subtle/70 bg-card/40 md:flex"
          >
            <SidebarContent>
              <SidebarGroup>
                <SidebarGroupContent>
                  <div className="px-3 pt-4 pb-2">
                    <h2 className="text-foreground typo-h4">Settings</h2>
                  </div>
                  <SidebarMenu>
                    {settingsSections.map((section) => {
                      const Icon = section.icon;
                      return (
                        <SidebarMenuItem key={section.key}>
                          <SidebarMenuButton asChild isActive={section.key === activeSection}>
                            <button
                              type="button"
                              aria-current={section.key === activeSection ? "true" : undefined}
                              onClick={() => setActiveSection(section.key)}
                            >
                              <Icon />
                              <span>{section.label}</span>
                            </button>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      );
                    })}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            </SidebarContent>
          </Sidebar>

          <main className="flex min-h-0 h-(--settings-dialog-height-desktop) flex-1 flex-col overflow-hidden">
            <SectionContent
              isDark={isDark}
              onToggleTheme={toggleTheme}
              activeSection={activeSection}
              isMobile={false}
            />
          </main>
        </SidebarProvider>
      </DialogContent>
    </Dialog>
  );
}
