/**
 * 404 — Not Found page.
 *
 * Renders as a full-viewport centered layout so it works both:
 *   1. Inside the app shell (wildcard `*` route) — fills available space
 *   2. Standalone at `/404` (outside the shell) — fills the viewport
 *
 * Offers a link back to the home page (Chat tab).
 */
import { useNavigate, Link } from "react-router";
import { FileQuestion, ArrowLeft, Home } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { springs } from "@/lib/config/motion-config";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/shared/BrandMark";

export function NotFoundPage() {
  const navigate = useNavigate();
  const shouldReduceMotion = useReducedMotion();

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-8">
      <motion.div
        initial={{ opacity: 0, y: shouldReduceMotion ? 0 : 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={shouldReduceMotion ? springs.instant : springs.default}
        className="flex flex-col items-center text-center gap-6 max-w-95"
      >
        <BrandMark className="w-8 h-3.75 text-foreground" />
        <div className="rounded-card-token flex h-3.75 w-3.75 items-center justify-center bg-muted">
          <FileQuestion className="w-7 h-7 text-muted-foreground" />
        </div>
        <div>
          <p className="text-muted-foreground mb-2 typo-display">404</p>
          <h1 className="text-foreground mb-2 typo-h3">Page not found</h1>
          <p className="text-muted-foreground typo-caption">
            The page you&rsquo;re looking for doesn&rsquo;t exist or has been moved. Check the URL
            or head back home.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row items-center gap-3 w-full">
          <Button
            variant="default"
            className="w-full sm:flex-1"
            onClick={() => navigate("/", { replace: true })}
          >
            <Home className="size-4" />
            <span className="typo-label">Back to Home</span>
          </Button>
          <Button variant="secondary" className="w-full sm:flex-1" onClick={() => navigate(-1)}>
            <ArrowLeft className="size-4" />
            <span className="typo-label">Go Back</span>
          </Button>
        </div>
        <Link
          to="/login"
          className="text-muted-foreground transition-colors hover:text-foreground typo-helper"
        >
          Need help? Contact support
        </Link>
      </motion.div>
    </div>
  );
}
