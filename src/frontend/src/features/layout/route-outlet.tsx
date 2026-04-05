import { Suspense } from "react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { ErrorBoundary } from "@/components/patterns/error-boundary";
import { PageSkeleton } from "@/components/patterns/page-skeleton";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { fades } from "@/lib/utils/motion";

export function LayoutRouteOutlet() {
  const routerState = useRouterState();
  const location = routerState.location;
  const isMobile = useIsMobile();
  const prefersReduced = useReducedMotion();
  const section = location.pathname.split("/").filter(Boolean)[1] || "workspace";

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={section}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={prefersReduced ? fades.instant : fades.fast}
            className="h-full w-full overflow-hidden"
          >
            <ErrorBoundary name={section}>
              <Suspense fallback={<PageSkeleton isMobile={isMobile} />}>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

export { LayoutRouteOutlet as ShellRouteOutlet };
