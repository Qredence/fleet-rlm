import { useEffect, useState } from "react";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowRight, LogOut } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { logoutWithEntra } from "@/lib/auth/entra";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { fades, springs } from "@/lib/utils/motion";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/logout")({
  component: LogoutScreen,
  errorComponent: RouteErrorScreen,
});

type LogoutPhase = "signing-out" | "done";

function LogoutScreen() {
  const navigate = useNavigate();
  const telemetry = useTelemetry();
  const prefersReduced = useReducedMotion();
  const [phase, setPhase] = useState<LogoutPhase>("signing-out");

  useEffect(() => {
    telemetry.capture("user_logged_out");
    telemetry.reset();

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    void logoutWithEntra()
      .catch(() => undefined)
      .then(() => {
        if (cancelled) return;
        timer = setTimeout(() => {
          setPhase("done");
        }, 500);
      });

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [telemetry]);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <Card className="w-full max-w-100 gap-6 shadow-(--shadow-200-stronger)">
        <AnimatePresence mode="wait">
          {phase === "signing-out" ? (
            <motion.div
              key="signing-out"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={prefersReduced ? fades.instant : fades.fast}
              className="flex flex-col gap-5"
            >
              <CardHeader className="items-center text-center">
                <BrandMark className="h-3.75 w-8 text-foreground" />
                <div className="flex flex-col gap-1">
                  <CardTitle className="text-foreground typo-h3">
                    Signing you out
                  </CardTitle>
                  <CardDescription className="typo-caption">
                    Clearing your local session and closing the current
                    workspace.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col items-center gap-3 pb-8">
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
                  className="rounded-full-token h-6 w-6 border-2 border-muted-foreground border-t-foreground"
                />
                <p className="text-muted-foreground typo-caption">
                  Signing you out&hellip;
                </p>
              </CardContent>
            </motion.div>
          ) : (
            <motion.div
              key="done"
              initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={prefersReduced ? springs.instant : springs.default}
              className="flex flex-col gap-5"
            >
              <CardHeader className="items-center text-center">
                <BrandMark className="h-3.75 w-8 text-foreground" />
                <div className="rounded-token flex h-12 w-12 items-center justify-center bg-muted">
                  <LogOut className="h-5 w-5 text-muted-foreground" />
                </div>
                <div className="flex flex-col gap-1">
                  <CardTitle className="text-foreground typo-h3">
                    You&rsquo;ve been signed out
                  </CardTitle>
                  <CardDescription className="typo-caption">
                    Your session has ended. Sign in again to continue.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardFooter className="flex flex-col items-stretch gap-3">
                <Button
                  className="w-full"
                  onClick={() => navigate({ to: "/login", replace: true })}
                >
                  <span className="typo-label">Sign In</span>
                  <ArrowRight className="size-4" />
                </Button>
                <Link
                  to="/login"
                  className="inline-flex items-center justify-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground typo-caption"
                >
                  Return to Microsoft sign-in
                </Link>
              </CardFooter>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </div>
  );
}
