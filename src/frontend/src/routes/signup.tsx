import { useState } from "react";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft, Loader2 } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTelemetry } from "@/lib/telemetry/use-telemetry";
import { springs } from "@/lib/utils/motion";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/signup")({
  component: SignupScreen,
  errorComponent: RouteErrorScreen,
});

function SignupScreen() {
  const navigate = useNavigate();
  const telemetry = useTelemetry();
  const prefersReduced = useReducedMotion();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }
    setLoading(true);
    await new Promise((resolve) => setTimeout(resolve, 1000));
    setLoading(false);

    telemetry.capture("user_signed_up", {
      source: "signup_page",
      has_name: Boolean(name.trim()),
    });

    navigate({ to: "/app/workspace", replace: true });
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <motion.div
        initial={{ opacity: 0, y: prefersReduced ? 0 : 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={prefersReduced ? springs.instant : springs.default}
        className="surface-raised-card w-full max-w-100 border border-border-subtle p-8"
      >
        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div className="flex flex-col items-center gap-3 pb-2">
            <BrandMark className="h-3.75 w-8 text-foreground" />
            <div className="text-center">
              <h1 className="text-sm font-medium text-foreground">
                Create your account
              </h1>
              <p className="mt-1 text-muted-foreground typo-caption">
                Get started with Skill Fleet
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="signup-name" className="typo-label">
              Full name
            </Label>
            <Input
              id="signup-name"
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Alex Chen"
              required
              autoComplete="name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="signup-email" className="typo-label">
              Email
            </Label>
            <Input
              id="signup-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@company.com"
              required
              autoComplete="email"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="signup-password" className="typo-label">
              Password
            </Label>
            <Input
              id="signup-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="At least 6 characters"
              required
              autoComplete="new-password"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="signup-confirm" className="typo-label">
              Confirm password
            </Label>
            <Input
              id="signup-confirm"
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              placeholder="Repeat your password"
              required
              autoComplete="new-password"
            />
          </div>
          {error ? (
            <motion.p
              initial={{ opacity: 0, y: prefersReduced ? 0 : -4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={prefersReduced ? springs.instant : springs.snappy}
              className="text-center text-destructive typo-caption"
              role="alert"
            >
              {error}
            </motion.p>
          ) : null}
          <Button
            type="submit"
            className="w-full"
            disabled={
              loading || !email || !name || !password || !confirmPassword
            }
          >
            {loading ? (
              <>
                <Loader2
                  className="size-5 animate-spin motion-reduce:animate-none"
                  strokeWidth={1.5}
                />
                <span className="typo-label">Creating account...</span>
              </>
            ) : (
              <span className="typo-label">Create Account</span>
            )}
          </Button>
          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-border-subtle" />
            <span className="text-muted-foreground typo-helper">or</span>
            <div className="h-px flex-1 bg-border-subtle" />
          </div>
          <div className="text-center">
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground typo-caption"
            >
              <ArrowLeft className="size-5" strokeWidth={1.5} />
              Already have an account? Sign in
            </Link>
          </div>
          <p className="text-center text-muted-foreground typo-helper">
            Demo mode &mdash; any details will work
          </p>
        </form>
      </motion.div>
    </div>
  );
}
