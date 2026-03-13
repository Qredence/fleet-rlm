/**
 * Standalone signup page at `/signup`.
 *
 * Full-screen centered registration form with the app logo. Mirrors the
 * LoginPage visual language. On successful signup the user is redirected
 * to `/`. This page lives outside the app shell (no header, no tabs,
 * no AuthProvider) -- it's entirely self-contained.
 *
 * Since the app uses mock auth, this page is mainly for demonstrating
 * the signup flow / direct URL access.
 */
import { useState } from "react";
import { useNavigate, Link } from "react-router";
import { Loader2, ArrowLeft } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { springs } from "@/lib/config/motion-config";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BrandMark } from "@/components/shared/BrandMark";

function SignupPage() {
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
    await new Promise((r) => setTimeout(r, 1000));
    setLoading(false);

    // Anonymous-only telemetry: capture signup event without PII.
    telemetry.capture("user_signed_up", {
      source: "signup_page",
      has_name: Boolean(name.trim()),
    });

    navigate("/", { replace: true });
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
            <BrandMark className="w-8 h-3.75 text-foreground" />
            <div className="text-center">
              <h1 className="text-foreground typo-h3">
                Create your account
              </h1>
              <p className="text-muted-foreground mt-1 typo-caption">
                Get started with Skill Fleet
              </p>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="signup-name" className="typo-label">
              Full name
            </Label>
            <Input
              id="signup-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Alex Chen"
              required
              autoComplete="name"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="signup-email" className="typo-label">
              Email
            </Label>
            <Input
              id="signup-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              required
              autoComplete="email"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="signup-password" className="typo-label">
              Password
            </Label>
            <Input
              id="signup-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 6 characters"
              required
              autoComplete="new-password"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="signup-confirm" className="typo-label">
              Confirm password
            </Label>
            <Input
              id="signup-confirm"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Repeat your password"
              required
              autoComplete="new-password"
            />
          </div>
          {error && (
            <motion.p
              initial={{ opacity: 0, y: prefersReduced ? 0 : -4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={prefersReduced ? springs.instant : springs.snappy}
              className="text-destructive text-center typo-caption"
              role="alert"
            >
              {error}
            </motion.p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={
              loading || !email || !name || !password || !confirmPassword
            }
          >
            {loading ? (
              <>
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                <span className="typo-label">Creating account...</span>
              </>
            ) : (
              <span className="typo-label">Create Account</span>
            )}
          </Button>
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-border-subtle" />
            <span className="text-muted-foreground typo-helper">
              or
            </span>
            <div className="flex-1 h-px bg-border-subtle" />
          </div>
          <div className="text-center">
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground typo-caption"
            >
              <ArrowLeft className="size-3.5" />
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

export { SignupPage };
