import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { isEntraAuthConfigured, loginWithEntra } from "@/lib/auth/entra";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/login")({
  component: LoginScreen,
  errorComponent: RouteErrorScreen,
});

function LoginScreen() {
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
      navigate({ to: "/app/workspace", replace: true });
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
      <div className="surface-raised-card w-full max-w-100 border border-border-subtle p-8">
        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div className="flex flex-col items-center gap-3 pb-2">
            <BrandMark className="h-3.75 w-8 text-foreground" />
            <div className="text-center">
              <h1 className="text-sm font-medium text-foreground">
                Sign in to Fleet RLM
              </h1>
              <p className="mt-1 text-muted-foreground typo-caption">
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
                <span className="typo-label">Opening Microsoft sign-in...</span>
              </>
            ) : (
              <span className="typo-label">Continue with Microsoft</span>
            )}
          </Button>
          {error ? (
            <p className="text-center text-destructive typo-helper">{error}</p>
          ) : null}
          <div className="text-center">
            <Link
              to="/signup"
              className="text-muted-foreground transition-colors hover:text-foreground typo-caption"
            >
              Need access? Contact your workspace administrator
            </Link>
          </div>
          <p className="text-center text-muted-foreground typo-helper">
            {authConfigured
              ? "The same access token is reused for API and WebSocket runtime sessions."
              : "Set the Entra client id, requested scopes, and optional authority override to enable Microsoft sign-in."}
          </p>
        </form>
      </div>
    </div>
  );
}
