import { AlertTriangle, Home, RotateCcw } from "lucide-react";
import { Link, useRouter, useRouterState } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { cn } from "@/lib/utils";

function extractErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "An unexpected routing error occurred.";
}

export function RouteErrorScreen({ error, reset }: { error: unknown; reset?: () => void }) {
  const routerState = useRouterState();
  const router = useRouter();
  const telemetry = useTelemetry();

  if (error) {
    telemetry.captureException(error);
  }

  const message = extractErrorMessage(error);
  const status = "500";

  return (
    <div className="font-app flex min-h-dvh w-full items-center justify-center bg-background px-6">
      <div className="mx-auto flex w-full max-w-xl flex-col items-center rounded-card border border-subtle bg-card px-6 py-10 text-center shadow-sm">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-destructive/10">
          <AlertTriangle className="size-6 text-destructive" aria-hidden="true" />
        </div>

        <p className="mb-1 text-muted-foreground typo-label">Route Error {status}</p>
        <h1 className="mb-3 text-sm font-medium text-foreground">
          We hit a rendering issue on this route
        </h1>
        <p className="mb-6 max-w-md text-muted-foreground typo-caption">
          The page failed while loading{" "}
          <span className="text-foreground">{routerState.location.pathname}</span>. You can retry,
          go back home, or continue in another section.
        </p>

        <pre
          className={cn(
            "mb-6 w-full max-w-md overflow-x-auto rounded-lg bg-muted px-4 py-3 text-left text-muted-foreground",
            "text-xs",
            "typo-mono",
          )}
        >
          {message}
        </pre>

        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button
            variant="secondary"
            onClick={() => {
              reset?.();
              router.invalidate();
            }}
            aria-label="Retry route"
          >
            <RotateCcw className="size-4" aria-hidden="true" />
            Retry
          </Button>
          <Button render={<Link to="/app/workspace" aria-label="Go home" />}>
            <Home className="size-4" aria-hidden="true" />
            Go Home
          </Button>
        </div>
      </div>
    </div>
  );
}
