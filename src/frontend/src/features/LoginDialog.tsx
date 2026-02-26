/**
 * Mock login dialog.
 *
 * Simple email + password form that calls `useAuth().login()`.
 * Desktop: centered Dialog. Mobile: iOS 26 Liquid Glass bottom sheet.
 */
import { useState } from "react";
import { Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { Drawer } from "vaul";
import { typo } from "@/lib/config/typo";
import { useAuth } from "@/hooks/useAuth";
import { useIsMobile } from "@/components/ui/use-mobile";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { IconButton } from "@/components/ui/icon-button";
import { BrandMark } from "@/components/shared/BrandMark";

// ── Shared form body ────────────────────────────────────────────────

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const { login } = useAuth();
  const [email, setEmail] = useState("alex@qredence.ai");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    const ok = await login(email, password);
    setLoading(false);
    if (ok) {
      toast.success("Signed in successfully");
      onSuccess();
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Logo + Title */}
      <div className="flex flex-col items-center gap-3 pb-2">
        <BrandMark className="w-8 h-[15px] text-foreground" />
        <div className="text-center">
          <h2 className="text-foreground" style={typo.h3}>
            Sign in to Skill Fleet
          </h2>
          <p className="text-muted-foreground mt-1" style={typo.caption}>
            Enter your credentials to continue
          </p>
        </div>
      </div>

      {/* Email */}
      <div className="space-y-1.5">
        <Label htmlFor="login-email" style={typo.label}>
          Email
        </Label>
        <Input
          id="login-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
          required
          autoComplete="email"
        />
      </div>

      {/* Password */}
      <div className="space-y-1.5">
        <Label htmlFor="login-password" style={typo.label}>
          Password
        </Label>
        <Input
          id="login-password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Enter password"
          required
          autoComplete="current-password"
        />
      </div>

      {/* Forgot password */}
      <div className="flex justify-end">
        <button
          type="button"
          className="text-accent bg-transparent border-0 p-0 cursor-pointer"
          style={typo.caption}
          onClick={() => toast("Password reset link sent (mock)")}
        >
          Forgot password?
        </button>
      </div>

      {/* Submit */}
      <Button type="submit" className="w-full" disabled={loading || !email}>
        {loading ? (
          <>
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
            <span style={typo.label}>Signing in...</span>
          </>
        ) : (
          <span style={typo.label}>Sign In</span>
        )}
      </Button>

      {/* Demo hint */}
      <p className="text-center text-muted-foreground" style={typo.helper}>
        Demo mode — any credentials will work
      </p>
    </form>
  );
}

// ── Main component ──────────────────────────────────────────────────

interface LoginDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function LoginDialog({ open, onOpenChange }: LoginDialogProps) {
  const isMobile = useIsMobile();

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
              maxHeight: "85dvh",
              borderTopLeftRadius: "var(--radius-card)",
              borderTopRightRadius: "var(--radius-card)",
              backgroundColor: "var(--glass-sheet-bg)",
              backdropFilter: "blur(var(--glass-sheet-blur))",
              WebkitBackdropFilter: "blur(var(--glass-sheet-blur))",
              borderTop: "0.5px solid var(--glass-sheet-border)",
            }}
          >
            {/* Grab handle */}
            <div className="flex items-center justify-center py-2 shrink-0">
              <div
                className="w-9 h-[5px] rounded-full"
                style={{ backgroundColor: "var(--glass-sheet-handle)" }}
                aria-hidden="true"
              />
            </div>

            <div className="flex items-center justify-between px-4 pb-2 shrink-0">
              <Drawer.Title>
                <span className="text-foreground" style={typo.h3}>
                  Sign In
                </span>
              </Drawer.Title>
              <IconButton
                onClick={() => onOpenChange(false)}
                aria-label="Close login"
                className="touch-target"
              >
                <X className="size-5 text-muted-foreground" />
              </IconButton>
            </div>
            <Drawer.Description className="sr-only">
              Sign in to your Skill Fleet account
            </Drawer.Description>

            <div className="px-4 pb-6 pt-2">
              <LoginForm onSuccess={() => onOpenChange(false)} />
            </div>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[400px] p-6 rounded-card">
        <DialogTitle className="sr-only">Sign In</DialogTitle>
        <DialogDescription className="sr-only">
          Sign in to your Skill Fleet account
        </DialogDescription>
        <LoginForm onSuccess={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}
