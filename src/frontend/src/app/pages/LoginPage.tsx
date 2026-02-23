/**
 * Standalone login page at `/login`.
 *
 * Full-screen centered login form with the app logo. On successful login
 * the user is redirected to `/`. This page lives outside the app shell
 * (no header, no tabs, no AuthProvider) — it's entirely self-contained.
 *
 * Since the app uses mock auth (user starts logged-in), this page is
 * mainly for demonstrating the login flow / direct URL access.
 */
import { useNavigate, Link } from "react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import headerSvg from "@/imports/svg-synwn0xtnf";

export { LoginPage };

function LoginPage() {
  const navigate = useNavigate();
  const telemetry = useTelemetry();
  const [email, setEmail] = useState("alex@qredence.ai");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    await new Promise((r) => setTimeout(r, 800));
    setLoading(false);

    // Anonymous-only telemetry: capture login event without PII.
    telemetry.capture("user_logged_in", {
      source: "login_page",
    });

    navigate("/", { replace: true });
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div
        className="w-full max-w-[400px] border border-border-subtle p-8"
        style={{
          borderRadius: "var(--radius-card)",
          boxShadow: "var(--shadow-200-stronger)",
          backgroundColor: "var(--card)",
        }}
      >
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="flex flex-col items-center gap-3 pb-2">
            <div className="w-8 h-[15px]">
              <svg
                className="block size-full"
                fill="none"
                preserveAspectRatio="xMidYMid meet"
                viewBox="0 0 18 17"
              >
                <path
                  clipRule="evenodd"
                  d={headerSvg.p4dc2a80}
                  fill="var(--foreground)"
                  fillRule="evenodd"
                />
              </svg>
            </div>
            <div className="text-center">
              <h1 className="text-foreground" style={typo.h3}>
                Sign in to Skill Fleet
              </h1>
              <p className="text-muted-foreground mt-1" style={typo.caption}>
                Enter your credentials to continue
              </p>
            </div>
          </div>
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
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-border-subtle" />
            <span className="text-muted-foreground" style={typo.helper}>
              or
            </span>
            <div className="flex-1 h-px bg-border-subtle" />
          </div>
          <div className="text-center">
            <Link
              to="/signup"
              className="text-muted-foreground transition-colors hover:text-foreground"
              style={typo.caption}
            >
              Don&rsquo;t have an account? Sign up
            </Link>
          </div>
          <p className="text-center text-muted-foreground" style={typo.helper}>
            Demo mode — any credentials will work
          </p>
        </form>
      </div>
    </div>
  );
}
