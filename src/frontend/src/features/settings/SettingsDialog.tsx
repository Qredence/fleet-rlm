import { useEffect, useMemo, useState } from "react";
import { Drawer } from "vaul";
import { X } from "lucide-react";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils/cn";
import { SettingsPaneContent } from "@/features/settings/SettingsPaneContent";
import {
  settingsSections,
  type SettingsSection,
} from "@/features/settings/types";
import { useNavigation } from "@/hooks/useNavigation";
import { typo } from "@/lib/config/typo";

const sectionDescriptions: Record<SettingsSection, string> = {
  appearance: "Control theme and interface appearance.",
  telemetry: "Configure anonymous telemetry preferences.",
  litellm:
    "Manage LiteLLM-compatible runtime model and provider integration settings.",
  runtime: "Configure runtime credentials and run Modal/LM connection tests.",
};

interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialSection?: SettingsSection;
}

function resolveInitialSection(
  section: SettingsSection | undefined,
): SettingsSection {
  return section &&
    settingsSections.some((candidate) => candidate.key === section)
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
  const activeMeta = useMemo(
    () => settingsSections.find((section) => section.key === activeSection),
    [activeSection],
  );

  return (
    <div
      className={cn("flex flex-col min-h-0", isMobile ? "h-[85dvh]" : "h-full")}
    >
      <div className="shrink-0 border-b border-border-subtle">
        <div className={isMobile ? "px-4 pt-3 pb-3" : "px-6 pt-5 pb-3"}>
          {isMobile ? (
            <span className="text-foreground" style={typo.h4}>
              {activeMeta?.label}
            </span>
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
          <p className="mt-1 text-sm text-muted-foreground">
            {sectionDescriptions[activeSection]}
          </p>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className={isMobile ? "px-4" : "px-6"}>
          <SettingsPaneContent
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
  return (
    <div className="shrink-0 overflow-x-auto border-b border-border-subtle px-4 py-2">
      <div className="flex w-max items-center gap-2">
        {settingsSections.map((section) => {
          const Icon = section.icon;
          const isActive = section.key === activeSection;
          return (
            <Button
              key={section.key}
              variant={isActive ? "secondary" : "ghost"}
              size="sm"
              className="touch-target rounded-full"
              onClick={() => onSelectSection(section.key)}
            >
              <Icon className="size-4" />
              {section.label}
            </Button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * SettingsDialog — theme and preference configuration.
 *
 * Consumes `isDark` / `toggleTheme` from NavigationContext.
 * `open` / `onOpenChange` are required props; `initialSection` optionally
 * focuses a specific settings section when opening.
 */
export function SettingsDialog({
  open,
  onOpenChange,
  initialSection,
}: SettingsDialogProps) {
  const { isDark, toggleTheme } = useNavigation();
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
                className="h-1.25 w-9 rounded-full"
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

            <MobileSectionNav
              activeSection={activeSection}
              onSelectSection={setActiveSection}
            />

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
      <DialogContent className="overflow-hidden p-0 md:max-h-130 md:max-w-195 lg:max-w-215">
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <DialogDescription className="sr-only">
          Configure Skill Fleet preferences and appearance
        </DialogDescription>
        <SidebarProvider className="items-start">
          <Sidebar collapsible="none" className="hidden border-r md:flex">
            <SidebarContent>
              <SidebarGroup>
                <SidebarGroupContent>
                  <div className="px-3 pt-4 pb-2">
                    <h2 className="text-foreground" style={typo.h4}>
                      Settings
                    </h2>
                  </div>
                  <SidebarMenu>
                    {settingsSections.map((section) => {
                      const Icon = section.icon;
                      return (
                        <SidebarMenuItem key={section.key}>
                          <SidebarMenuButton
                            asChild
                            isActive={section.key === activeSection}
                          >
                            <button
                              type="button"
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

          <main className="flex h-125 min-h-0 flex-1 flex-col overflow-hidden">
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
