import { AlertTriangle, Home, RotateCcw } from "lucide-react";
import {
  isRouteErrorResponse,
  Link,
  useLocation,
  useNavigate,
  useRouteError,
} from "react-router";
import { usePostHog } from "@posthog/react";
import { typo } from "../components/config/typo";
import { Button } from "../components/ui/button";
import { cn } from "../components/ui/utils";

function extractErrorMessage(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return error.data?.message || error.statusText || `HTTP ${error.status}`;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "An unexpected routing error occurred.";
}

function extractStatus(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return `${error.status}`;
  }
  return "500";
}

export function RouteErrorPage() {
  const error = useRouteError();
  const navigate = useNavigate();
  const location = useLocation();
  const posthog = usePostHog();

  // PostHog: Capture route errors for error tracking
  if (error) {
    posthog?.captureException(error);
  }

  const message = extractErrorMessage(error);
  const status = extractStatus(error);

  return (
    <div
      className="flex min-h-dvh w-full items-center justify-center bg-background px-6"
      style={{ fontFamily: "var(--font-family)" }}
    >
      <div className="mx-auto flex w-full max-w-xl flex-col items-center rounded-card border border-border-subtle bg-card px-6 py-10 text-center shadow-sm">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-destructive/10">
          <AlertTriangle
            className="size-6 text-destructive"
            aria-hidden="true"
          />
        </div>

        <p className="mb-1 text-muted-foreground" style={typo.label}>
          Route Error {status}
        </p>
        <h1 className="mb-3 text-foreground" style={typo.h3}>
          We hit a rendering issue on this route
        </h1>
        <p className="mb-6 max-w-md text-muted-foreground" style={typo.caption}>
          The page failed while loading{" "}
          <span className="text-foreground">{location.pathname}</span>. You can
          retry, go back home, or continue in another section.
        </p>

        <pre
          className={cn(
            "mb-6 w-full max-w-md overflow-x-auto rounded-lg bg-muted px-4 py-3 text-left text-muted-foreground",
            "text-xs",
          )}
          style={typo.mono}
        >
          {message}
        </pre>

        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button
            variant="secondary"
            onClick={() => navigate(0)}
            aria-label="Retry route"
          >
            <RotateCcw className="size-4" aria-hidden="true" />
            Retry
          </Button>
          <Button asChild>
            <Link to="/" aria-label="Go home">
              <Home className="size-4" aria-hidden="true" />
              Go Home
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
