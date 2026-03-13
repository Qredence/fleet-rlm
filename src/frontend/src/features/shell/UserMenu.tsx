/**
 * User avatar dropdown menu.
 *
 * When authenticated: avatar opens a dropdown with profile info,
 * Settings, Pricing, Integrations, and Logout.
 *
 * When logged out: shows a "Sign In" button.
 */
import { useState, useEffect, useRef } from "react";
import {
  Settings,
  CreditCard,
  Blocks,
  LogOut,
  ChevronDown,
  LogIn,
} from "lucide-react";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { useAuth } from "@/hooks/useAuth";
import { useIsMobile } from "@/hooks/useIsMobile";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SettingsDialog } from "@/features/settings/SettingsDialog";
import { PricingDialog } from "./PricingDialog";
import { IntegrationsDialog } from "./IntegrationsDialog";
import { LoginDialog } from "./LoginDialog";
import type { SettingsSection } from "@/features/settings/types";
import { cn } from "@/lib/utils/cn";

interface OpenSettingsEventDetail {
  section?: SettingsSection;
}

export function UserMenu() {
  const { isAuthenticated, user, logout } = useAuth();
  const telemetry = useTelemetry();
  const isMobile = useIsMobile();

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsInitialSection, setSettingsInitialSection] = useState<
    SettingsSection | undefined
  >(undefined);
  const [pricingOpen, setPricingOpen] = useState(false);
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const loginTriggerRef = useRef<HTMLButtonElement>(null);

  // Listen for Command Palette "open-settings" custom event
  useEffect(() => {
    function handleOpenSettings(event: Event) {
      const customEvent = event as CustomEvent<OpenSettingsEventDetail>;
      setSettingsInitialSection(customEvent.detail?.section);
      setSettingsOpen(true);
      customEvent.preventDefault();
    }
    document.addEventListener("open-settings", handleOpenSettings);
    return () =>
      document.removeEventListener("open-settings", handleOpenSettings);
  }, []);

  /* ── Logged-out state ──────────────────────────────────────────── */
  if (!isAuthenticated || !user) {
    return (
      <>
        <Button
          ref={loginTriggerRef}
          variant="secondary"
          className="gap-1.5"
          onClick={() => setLoginOpen(true)}
        >
          <LogIn className="size-4" />
          <span className="typo-label">Sign In</span>
        </Button>

        <LoginDialog
          open={loginOpen}
          onOpenChange={setLoginOpen}
          returnFocusRef={loginTriggerRef}
        />

        <SettingsDialog
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          initialSection={settingsInitialSection}
        />
      </>
    );
  }

  /* ── Logged-in state ───────────────────────────────────────────── */
  const planLabel = user.plan.charAt(0).toUpperCase() + user.plan.slice(1);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className={cn(
              "flex items-center gap-1.5 rounded-lg p-1 transition-colors",
              "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              isMobile && "touch-target justify-center",
            )}
            aria-label="User menu"
          >
            <Avatar className="size-7">
              <AvatarFallback className="font-app bg-accent/10 text-[length:var(--font-text-2xs-size)] font-medium leading-[var(--font-text-2xs-line-height)] tracking-[var(--font-text-2xs-tracking)] text-accent">
                {user.initials}
              </AvatarFallback>
            </Avatar>
            {!isMobile && (
              <ChevronDown className="size-3 text-muted-foreground" />
            )}
          </button>
        </DropdownMenuTrigger>

        <DropdownMenuContent align="end" className="w-56">
          {/* User info header */}
          <DropdownMenuLabel className="p-3">
            <div className="flex items-center gap-2.5">
              <Avatar className="size-9">
                <AvatarFallback className="font-app bg-accent/10 text-[length:var(--font-text-2xs-size)] font-medium leading-[var(--font-text-2xs-line-height)] tracking-[var(--font-text-2xs-tracking)] text-accent">
                  {user.initials}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1 min-w-0">
                <span
                  className="text-foreground block truncate typo-label"
                >
                  {user.name}
                </span>
                <span
                  className="text-muted-foreground block truncate typo-helper"
                >
                  {user.email}
                </span>
              </div>
            </div>
            <div className="mt-2">
              <Badge variant="accent">{planLabel} Plan</Badge>
            </div>
          </DropdownMenuLabel>

          <DropdownMenuSeparator />

          <DropdownMenuGroup>
            <DropdownMenuItem
              onClick={() => {
                // PostHog: Capture settings opened event
                telemetry.capture("settings_opened", {
                  source: "user_menu",
                });
                setSettingsInitialSection(undefined);
                setSettingsOpen(true);
              }}
              className="typo-label"
            >
              <Settings className="size-4" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setPricingOpen(true)}
              className="typo-label"
            >
              <CreditCard className="size-4" />
              Pricing Plan
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setIntegrationsOpen(true)}
              className="typo-label"
            >
              <Blocks className="size-4" />
              Integrations
            </DropdownMenuItem>
          </DropdownMenuGroup>

          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={() => {
              logout();
            }}
            variant="destructive"
            className="typo-label"
          >
            <LogOut className="size-4" />
            Sign Out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Dialogs — rendered outside dropdown to avoid portal nesting */}
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        initialSection={settingsInitialSection}
      />
      <PricingDialog open={pricingOpen} onOpenChange={setPricingOpen} />
      <IntegrationsDialog
        open={integrationsOpen}
        onOpenChange={setIntegrationsOpen}
      />
    </>
  );
}
