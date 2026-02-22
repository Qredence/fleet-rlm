/**
 * Standalone logout page at `/logout`.
 *
 * Shows a brief "signing out" state, then transitions to a confirmation
 * screen with a redirect back to login. This page lives outside the app
 * shell -- it doesn't depend on AuthProvider or NavigationProvider.
 *
 * In a real app this would clear tokens / session cookies; here it just
 * simulates the transition for demo purposes.
 */
import { useState, useEffect } from "react";
import { useNavigate, Link } from "react-router";
import { LogOut, ArrowRight } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { usePostHog } from "@posthog/react";
import { typo } from "@/lib/config/typo";
import { springs, fades } from "@/lib/config/motion-config";
import { Button } from "@/components/ui/button";
import headerSvg from "@/imports/svg-synwn0xtnf";

type LogoutPhase = "signing-out" | "done";

function LogoutPage() {
  const navigate = useNavigate();
  const posthog = usePostHog();
  const prefersReduced = useReducedMotion();
  const [phase, setPhase] = useState<LogoutPhase>("signing-out");

  useEffect(() => {
    // PostHog: Capture logout event and reset user
    posthog?.capture("user_logged_out");
    posthog?.reset();

    const timer = setTimeout(() => {
      setPhase("done");
    }, 1200);
    return () => clearTimeout(timer);
  }, [posthog]);

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
        <AnimatePresence mode="wait">
          {phase === "signing-out" ? (
            <motion.div
              key="signing-out"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={prefersReduced ? fades.instant : fades.fast}
              className="flex flex-col items-center gap-5 py-4"
            >
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
              <div className="flex flex-col items-center gap-3">
                <motion.div
                  animate={prefersReduced ? undefined : { rotate: 360 }}
                  transition={
                    prefersReduced
                      ? springs.instant
                      : {
                          duration: 1,
                          repeat: Infinity,
                          ease: "linear",
                        }
                  }
                  className="w-6 h-6 border-2 border-muted-foreground border-t-foreground"
                  style={{ borderRadius: "50%" }}
                />
                <p className="text-muted-foreground" style={typo.caption}>
                  Signing you out&hellip;
                </p>
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="done"
              initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={prefersReduced ? springs.instant : springs.default}
              className="flex flex-col items-center gap-5 py-4"
            >
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
              <div
                className="w-12 h-12 flex items-center justify-center bg-muted"
                style={{ borderRadius: "var(--radius)" }}
              >
                <LogOut className="w-5 h-5 text-muted-foreground" />
              </div>
              <div className="text-center">
                <h1 className="text-foreground mb-1" style={typo.h3}>
                  You&rsquo;ve been signed out
                </h1>
                <p className="text-muted-foreground" style={typo.caption}>
                  Your session has ended. Sign in again to continue.
                </p>
              </div>
              <div className="flex flex-col gap-3 w-full">
                <Button
                  className="w-full"
                  onClick={() => navigate("/login", { replace: true })}
                >
                  <span style={typo.label}>Sign In</span>
                  <ArrowRight className="size-4" />
                </Button>
                <Link
                  to="/signup"
                  className="inline-flex items-center justify-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
                  style={typo.caption}
                >
                  Don&rsquo;t have an account? Sign up
                </Link>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

export { LogoutPage };
