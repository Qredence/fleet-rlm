/**
 * Microsoft Entra sign-in dialog.
 *
 * Uses the shared auth provider to start the Microsoft sign-in flow.
 * Desktop: centered Dialog. Mobile: iOS 26 Liquid Glass bottom sheet.
 */
import { useEffect, useRef, useState, type RefObject } from "react";
import { Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { isEntraAuthConfigured } from "@/lib/auth/entra";
import { useAuth } from "@/lib/auth/auth-context";
import { useIsMobile } from "@/hooks/use-is-mobile";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { BrandMark } from "@/components/brand-mark";

// ── Shared form body ────────────────────────────────────────────────

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const { login } = useAuth();
  const [loading, setLoading] = useState(false);
  const authConfigured = isEntraAuthConfigured();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!authConfigured) {
      toast.error(
        "Microsoft Entra sign-in is not configured. Set VITE_ENTRA_CLIENT_ID and VITE_ENTRA_SCOPES first.",
      );
      return;
    }
    setLoading(true);
    const ok = await login();
    setLoading(false);
    if (ok) {
      toast.success("Signed in successfully");
      onSuccess();
      return;
    }
    toast.error(
      "Microsoft sign-in failed. Check your Entra redirect URI, authority, and requested scopes.",
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      {/* Logo + Title */}
      <div className="flex flex-col items-center gap-3 pb-2">
        <BrandMark className="w-8 h-3.75 text-foreground" />
        <div className="text-center">
          <h2 className="text-foreground typo-h3">Sign in to Fleet RLM</h2>
          <p className="text-muted-foreground mt-1 typo-caption">
            Continue with your Microsoft Entra account
          </p>
        </div>
      </div>

      {/* Submit */}
      <Button
        type="submit"
        className="w-full"
        disabled={loading || !authConfigured}
      >
        {loading ? (
          <>
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
            <span className="typo-label">Opening Microsoft sign-in...</span>
          </>
        ) : (
          <span className="typo-label">Continue with Microsoft</span>
        )}
      </Button>

      <p className="text-center text-muted-foreground typo-helper">
        {authConfigured
          ? "Your Entra access token is reused for both API and WebSocket runtime calls."
          : "This workspace needs Entra SPA settings before Microsoft sign-in can be used."}
      </p>
    </form>
  );
}

// ── Main component ──────────────────────────────────────────────────

interface LoginDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function LoginDialog({
  open,
  onOpenChange,
  returnFocusRef,
}: LoginDialogProps) {
  const isMobile = useIsMobile();
  const wasOpenRef = useRef(open);

  useEffect(() => {
    if (wasOpenRef.current && !open) {
      returnFocusRef?.current?.focus();
    }
    wasOpenRef.current = open;
  }, [open, returnFocusRef]);

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="surface-glass-sheet inset-x-0 bottom-0 top-auto max-h-[85dvh] rounded-t-[calc(var(--radius-xl)+0.25rem)] border-t-0 px-0 pt-0 pb-0"
        >
          <div className="flex items-center justify-center py-2 shrink-0">
            <div
              className="surface-glass-handle h-1.25 w-9 rounded-full"
              aria-hidden="true"
            />
          </div>

          <div className="flex items-center justify-between px-4 pb-2 shrink-0">
            <SheetTitle className="text-foreground typo-h3">Sign In</SheetTitle>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => onOpenChange(false)}
              aria-label="Close login"
              className="touch-target"
            >
              <X className="size-5 text-muted-foreground" />
            </Button>
          </div>
          <SheetDescription className="sr-only">
            Sign in to your Fleet RLM account
          </SheetDescription>

          <div className="px-4 pb-6 pt-2">
            <LoginForm onSuccess={() => onOpenChange(false)} />
          </div>
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-100 p-6 rounded-card">
        <DialogTitle className="sr-only">Sign In</DialogTitle>
        <DialogDescription className="sr-only">
          Sign in to your Fleet RLM account
        </DialogDescription>
        <LoginForm onSuccess={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}
