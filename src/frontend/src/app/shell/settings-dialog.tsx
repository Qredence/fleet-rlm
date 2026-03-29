import * as React from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Sidebar, SidebarProvider } from "@/components/ui/sidebar";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useIsMobile } from "@/hooks/use-is-mobile";
import {
  getSettingsSectionDescription,
  getSettingsSectionTitle,
  resolveSettingsSection,
  SettingsSectionContent,
  SettingsSidebarNav,
  type SettingsSection,
} from "@/screens/settings/settings-content";
import { useThemeStore } from "@/stores/theme-store";

interface SettingsDialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  section?: SettingsSection;
  onSectionChange?: (section?: SettingsSection) => void;
  returnFocusRef?: React.RefObject<HTMLElement | null>;
  showTrigger?: boolean;
}

function MobileSectionPicker({
  section,
  onSectionChange,
}: {
  section?: SettingsSection;
  onSectionChange: (section?: SettingsSection) => void;
}) {
  return (
    <ToggleGroup
      type="single"
      value={section ?? "appearance"}
      variant="outline"
      size="sm"
      aria-label="Settings section"
      className="flex flex-wrap justify-start gap-2"
      onValueChange={(nextValue) => {
        if (!nextValue) return;
        onSectionChange(nextValue as SettingsSection);
      }}
    >
      <ToggleGroupItem value="appearance" aria-label="Appearance">
        Appearance
      </ToggleGroupItem>
      <ToggleGroupItem value="telemetry" aria-label="Telemetry">
        Telemetry
      </ToggleGroupItem>
      <ToggleGroupItem value="litellm" aria-label="LiteLLM Integration">
        LiteLLM
      </ToggleGroupItem>
      <ToggleGroupItem value="runtime" aria-label="Runtime">
        Runtime
      </ToggleGroupItem>
    </ToggleGroup>
  );
}

export function SettingsDialog({
  open,
  defaultOpen = true,
  onOpenChange,
  section,
  onSectionChange,
  returnFocusRef,
  showTrigger = false,
}: SettingsDialogProps) {
  const isMobile = useIsMobile();
  const { isDark, toggle: toggleTheme } = useThemeStore();
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const [activeSection, setActiveSection] = React.useState<SettingsSection | undefined>(
    resolveSettingsSection(section) ?? "appearance",
  );
  const resolvedOpen = open ?? uncontrolledOpen;
  const wasOpenRef = React.useRef(resolvedOpen);

  const handleOpenChange = React.useCallback(
    (nextOpen: boolean) => {
      if (open == null) {
        setUncontrolledOpen(nextOpen);
      }
      onOpenChange?.(nextOpen);
    },
    [onOpenChange, open],
  );

  React.useEffect(() => {
    if (wasOpenRef.current && !resolvedOpen) {
      returnFocusRef?.current?.focus();
    }
    wasOpenRef.current = resolvedOpen;
  }, [resolvedOpen, returnFocusRef]);

  React.useEffect(() => {
    if (!resolvedOpen) return;
    setActiveSection(resolveSettingsSection(section) ?? "appearance");
  }, [resolvedOpen, section]);

  const handleSectionChange = React.useCallback(
    (nextSection?: SettingsSection) => {
      const resolvedSection = nextSection ?? "appearance";
      setActiveSection(resolvedSection);
      onSectionChange?.(resolvedSection);
    },
    [onSectionChange],
  );

  const sectionTitle = getSettingsSectionTitle(activeSection);
  const sectionDescription = getSettingsSectionDescription(activeSection);

  if (isMobile) {
    return (
      <Sheet open={resolvedOpen} onOpenChange={handleOpenChange}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="inset-x-0 bottom-0 top-auto h-[min(90dvh,52rem)] gap-0 rounded-t-[calc(var(--radius-xl)+0.25rem)] border-x-0 border-b-0 px-0 pt-0 pb-0 sm:max-w-none"
        >
          <div className="flex items-center justify-center py-2 shrink-0">
            <div className="surface-glass-handle h-1.25 w-9 rounded-full" aria-hidden="true" />
          </div>

          <div className="flex items-center justify-between gap-3 px-4 pb-2 shrink-0">
            <div className="min-w-0">
              <SheetTitle className="text-foreground typo-h3">Settings</SheetTitle>
              <SheetDescription className="mt-1 text-sm text-muted-foreground">
                {sectionDescription}
              </SheetDescription>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => handleOpenChange(false)}
              aria-label="Close settings"
              className="touch-target shrink-0"
            >
              <X />
            </Button>
          </div>

          <div className="px-4 pb-4">
            <MobileSectionPicker section={activeSection} onSectionChange={handleSectionChange} />
          </div>

          <ScrollArea className="min-h-0 flex-1 border-t border-border-subtle">
            <div className="flex flex-col gap-4 px-4 py-4">
              <div>
                <h2 className="text-foreground typo-h4">{sectionTitle}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{sectionDescription}</p>
              </div>
              <SettingsSectionContent
                isDark={isDark}
                onToggleTheme={toggleTheme}
                section={activeSection}
              />
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <Dialog open={resolvedOpen} onOpenChange={handleOpenChange}>
      {showTrigger ? (
        <DialogTrigger asChild>
          <Button size="sm">Open Dialog</Button>
        </DialogTrigger>
      ) : null}
      <DialogContent className="overflow-hidden rounded-[1.75rem] border-border-subtle/80 bg-background p-0 md:max-h-[min(88vh,760px)] md:max-w-[1040px] lg:max-w-[1080px]">
        <SidebarProvider
          className="items-stretch"
          defaultOpen
          style={
            {
              "--sidebar-width": "15rem",
            } as React.CSSProperties
          }
        >
          <Sidebar
            collapsible="none"
            className="hidden border-r border-sidebar-border/70 bg-sidebar/40 md:flex"
          >
            <SettingsSidebarNav section={activeSection} onSectionChange={handleSectionChange} />
          </Sidebar>
          <main className="flex h-[min(82vh,760px)] min-w-0 flex-1 flex-col overflow-hidden bg-background">
            <header className="shrink-0 border-b border-border-subtle bg-background px-9 py-7">
              <DialogTitle className="text-[2rem] font-semibold tracking-tight text-foreground">
                Settings
              </DialogTitle>
              <DialogDescription className="sr-only">
                Manage appearance, telemetry, LiteLLM integration, and runtime preferences.
              </DialogDescription>
            </header>
            <ScrollArea className="flex-1">
              <div className="flex flex-col gap-6 px-9 py-8">
                <SettingsSectionContent
                  isDark={isDark}
                  onToggleTheme={toggleTheme}
                  section={activeSection}
                />
              </div>
            </ScrollArea>
          </main>
        </SidebarProvider>
      </DialogContent>
    </Dialog>
  );
}
