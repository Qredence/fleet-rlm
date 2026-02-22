/**
 * User avatar dropdown menu.
 *
 * When authenticated: avatar opens a dropdown with profile info,
 * Settings, Pricing, Integrations, and Logout.
 *
 * When logged out: shows a "Sign In" button.
 */
import { useState, useEffect } from "react";
import {
  Settings,
  CreditCard,
  Blocks,
  LogOut,
  ChevronDown,
  LogIn,
} from "lucide-react";
import { usePostHog } from "@posthog/react";
import { typo } from "@/lib/config/typo";
import { useAuth } from "@/hooks/useAuth";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/components/ui/use-mobile";
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
import { SettingsDialog } from "@/features/SettingsDialog";
import { PricingDialog } from "@/features/PricingDialog";
import { IntegrationsDialog } from "@/features/IntegrationsDialog";
import { LoginDialog } from "@/features/LoginDialog";
import { cn } from "@/components/ui/utils";

export function UserMenu() {
  const { isAuthenticated, user, logout } = useAuth();
  const { navigate } = useAppNavigate();
  const posthog = usePostHog();
  const isMobile = useIsMobile();

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pricingOpen, setPricingOpen] = useState(false);
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);

  // Listen for Command Palette "open-settings" custom event
  useEffect(() => {
    function handleOpenSettings() {
      setSettingsOpen(true);
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
          variant="secondary"
          className="gap-1.5"
          onClick={() => setLoginOpen(true)}
        >
          <LogIn className="size-4" />
          <span style={typo.label}>Sign In</span>
        </Button>

        <LoginDialog open={loginOpen} onOpenChange={setLoginOpen} />
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
              "hover:bg-muted focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
              isMobile && "touch-target justify-center",
            )}
            aria-label="User menu"
          >
            <Avatar className="size-7">
              <AvatarFallback
                className="bg-accent/10 text-accent"
                style={{
                  fontSize: "var(--text-helper)",
                  fontWeight: "var(--font-weight-medium)",
                  fontFamily: "var(--font-family)",
                }}
              >
                {user.initials}
              </AvatarFallback>
            </Avatar>
            {!isMobile && (
              <ChevronDown className="size-3 text-muted-foreground" />
            )}
          </button>
        </DropdownMenuTrigger>

        <DropdownMenuContent align="end" className="w-[220px]">
          {/* User info header */}
          <DropdownMenuLabel className="p-3">
            <div className="flex items-center gap-2.5">
              <Avatar className="size-9">
                <AvatarFallback
                  className="bg-accent/10 text-accent"
                  style={{
                    fontSize: "var(--text-helper)",
                    fontWeight: "var(--font-weight-medium)",
                    fontFamily: "var(--font-family)",
                  }}
                >
                  {user.initials}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1 min-w-0">
                <span
                  className="text-foreground block truncate"
                  style={typo.label}
                >
                  {user.name}
                </span>
                <span
                  className="text-muted-foreground block truncate"
                  style={typo.helper}
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
                posthog?.capture("settings_opened", {
                  source: "user_menu",
                });
                setSettingsOpen(true);
              }}
              style={typo.label}
            >
              <Settings className="size-4" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setPricingOpen(true)}
              style={typo.label}
            >
              <CreditCard className="size-4" />
              Pricing Plan
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setIntegrationsOpen(true)}
              style={typo.label}
            >
              <Blocks className="size-4" />
              Integrations
            </DropdownMenuItem>
          </DropdownMenuGroup>

          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={() => {
              logout();
              navigate("/logout");
            }}
            variant="destructive"
            style={typo.label}
          >
            <LogOut className="size-4" />
            Sign Out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Dialogs — rendered outside dropdown to avoid portal nesting */}
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <PricingDialog open={pricingOpen} onOpenChange={setPricingOpen} />
      <IntegrationsDialog
        open={integrationsOpen}
        onOpenChange={setIntegrationsOpen}
      />
    </>
  );
}
