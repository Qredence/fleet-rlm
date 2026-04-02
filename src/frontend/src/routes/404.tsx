import { SearchX, Home } from "lucide-react";
import { createFileRoute, Link } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/404")({
  component: RouteComponent,
});

function RouteComponent() {
  return (
    <div className="font-app flex min-h-dvh w-full items-center justify-center bg-background px-6">
      <div className="mx-auto flex w-full max-w-xl flex-col items-center rounded-card border border-subtle bg-card px-6 py-10 text-center shadow-sm">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-muted">
          <SearchX className="size-6 text-muted-foreground" aria-hidden="true" />
        </div>

        <p className="mb-1 text-muted-foreground typo-label">404</p>
        <h1 className="mb-3 text-sm font-medium text-foreground">Page not found</h1>
        <p className="mb-6 max-w-md text-muted-foreground typo-caption">
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
        </p>

        <Button render={<Link to="/app/workspace" aria-label="Go to workspace" />}>
          <Home className="size-4" aria-hidden="true" />
          Go to Workspace
        </Button>
      </div>
    </div>
  );
}
