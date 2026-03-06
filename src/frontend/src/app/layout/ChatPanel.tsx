/**
 * ChatPanel — main content area that renders the active route's component.
 *
 * Uses React Router's `<Outlet />` for content determined by the URL.
 * Wraps the outlet in AnimatePresence for smooth page transitions.
 *
 * The transition key is derived from the active `/app/*` child route
 * so that navigating within a surface (e.g. `/app/workspace` → `/app/volumes`)
 * does NOT re-trigger the page transition animation.
 */
import { Suspense } from "react";
import { Outlet, useLocation } from "react-router";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { fades } from "@/lib/config/motion-config";
import { useIsMobile } from "@/hooks/useIsMobile";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { PageSkeleton } from "@/components/shared/PageSkeleton";

export function ChatPanel() {
  const location = useLocation();
  const isMobile = useIsMobile();
  const prefersReduced = useReducedMotion();

  // Derive the "section" for AnimatePresence key —
  // only animate when changing top-level sections, not sub-routes
  const section =
    location.pathname.split("/").filter(Boolean)[1] || "workspace";

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Content router */}
      <div className="flex-1 min-h-0 min-w-0 overflow-hidden">
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
