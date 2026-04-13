import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { telemetryClient } from "@/lib/telemetry/client";
import { cn } from "@/lib/utils";

interface ErrorBoundaryProps {
  name?: string;
  fallback?: ReactNode;
  className?: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(
      `[ErrorBoundary${this.props.name ? `: ${this.props.name}` : ""}]`,
      error,
      info.componentStack,
    );

    telemetryClient.captureException(error);
    telemetryClient.capture("react_error_boundary_exception", {
      boundary_name: this.props.name ?? null,
      component_stack: info.componentStack,
    });
  }

  private handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      const label = this.props.name ?? "This section";

      return (
        <div
          className={cn(
            "flex h-full w-full flex-col items-center justify-center px-8 text-center",
            this.props.className,
          )}
          role="alert"
        >
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-destructive/10">
            <AlertTriangle className="size-6 text-destructive" aria-hidden="true" />
          </div>

          <h2 className="typo-label mb-1 text-foreground">{label} encountered an error</h2>

          <p className="typo-caption mb-6 max-w-sm text-muted-foreground">
            Something went wrong while rendering this view. You can try reloading it, or switch to a
            different tab.
          </p>

          {this.state.error ? (
            <pre className="typo-mono mb-6 w-full max-w-md overflow-x-auto rounded-lg bg-muted px-4 py-3 text-left text-muted-foreground">
              {this.state.error.message}
            </pre>
          ) : null}

          <Button
            variant="secondary"
            onClick={this.handleRetry}
            aria-label={`Retry loading ${label}`}
          >
            <RotateCcw className="size-4" aria-hidden="true" />
            Try again
          </Button>
        </div>
      );
    }

    return this.props.children;
  }
}

export { ErrorBoundary };
