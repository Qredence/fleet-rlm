/**
 * Standalone login page at `/login`.
 *
 * Full-screen centered Entra sign-in entrypoint. On successful login
 * the user is redirected to the authenticated workspace. This page lives outside the app shell
 * (no header, no tabs, no AuthProvider) — it's entirely self-contained.
 */
import { useNavigate, Link } from "react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { isEntraAuthConfigured, loginWithEntra } from "@/lib/auth/entra";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/shared/BrandMark";

export { LoginPage };

function LoginPage() {
  const navigate = useNavigate();
  const telemetry = useTelemetry();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const authConfigured = isEntraAuthConfigured();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!authConfigured) {
      setError(
        "Microsoft Entra sign-in is not configured. Set VITE_ENTRA_CLIENT_ID and VITE_ENTRA_SCOPES before using this page.",
      );
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await loginWithEntra();
      telemetry.capture("user_logged_in", {
        source: "login_page",
      });
      navigate("/app/workspace", { replace: true });
    } catch {
      setError(
        "Microsoft sign-in did not complete. Try again, then verify your Entra tenant, redirect URI, and requested scopes.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div
        className="w-full max-w-100 border border-border-subtle p-8"
        style={{
          borderRadius: "var(--radius-card)",
          boxShadow: "var(--shadow-200-stronger)",
          backgroundColor: "var(--card)",
        }}
      >
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="flex flex-col items-center gap-3 pb-2">
            <BrandMark className="w-8 h-3.75 text-foreground" />
            <div className="text-center">
              <h1 className="text-foreground" style={typo.h3}>
                Sign in to Fleet RLM
              </h1>
              <p className="text-muted-foreground mt-1" style={typo.caption}>
                Continue with Microsoft Entra to open your RLM workspace
              </p>
            </div>
          </div>
          <Button
            type="submit"
            className="w-full"
            disabled={loading || !authConfigured}
          >
            {loading ? (
              <>
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                <span style={typo.label}>Opening Microsoft sign-in...</span>
              </>
            ) : (
              <span style={typo.label}>Continue with Microsoft</span>
            )}
          </Button>
          {error ? (
            <p className="text-center text-destructive" style={typo.helper}>
              {error}
            </p>
          ) : null}
          <div className="text-center">
            <Link
              to="/signup"
              className="text-muted-foreground transition-colors hover:text-foreground"
              style={typo.caption}
            >
              Need access? Contact your workspace administrator
            </Link>
          </div>
          <p className="text-center text-muted-foreground" style={typo.helper}>
            {authConfigured
              ? "The same access token is reused for API and WebSocket runtime sessions."
              : "Set the Entra client id, requested scopes, and optional authority override to enable Microsoft sign-in."}
          </p>
        </form>
      </div>
    </div>
  );
}
