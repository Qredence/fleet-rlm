import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { telemetryClient } from "@/lib/telemetry/client";

// ── Types ───────────────────────────────────────────────────────────
interface ErrorBoundaryProps {
  /** Label shown in the error fallback (e.g. "Skill Library"). */
  name?: string;
  /** Optional custom fallback — replaces the default card. */
  fallback?: ReactNode;
  /** Additional className on the fallback wrapper. */
  className?: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

// ── Component ───────────────────────────────────────────────────────
/**
 * React error boundary that wraps page-level components.
 *
 * NOTE: Error boundaries *must* be class components — there is no
 * hook equivalent as of React 18.  This is the one place in the
 * project where a class component is acceptable.
 */
class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log to the console so developers can debug in preview.
    console.error(
      `[ErrorBoundary${this.props.name ? `: ${this.props.name}` : ""}]`,
      error,
      info.componentStack,
    );

    // Anonymous-only telemetry: capture component errors for error tracking
    telemetryClient.captureException(error);

    // PostHog: Also capture React-specific context for better debugging
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
            "flex flex-col items-center justify-center h-full w-full px-8 text-center",
            this.props.className,
          )}
          role="alert"
        >
          {/* Icon */}
          <div className="flex items-center justify-center w-12 h-12 rounded-lg bg-destructive/10 mb-4">
            <AlertTriangle className="size-6 text-destructive" aria-hidden="true" />
          </div>

          {/* Title */}
          <h2 className="text-foreground mb-1 typo-label">{label} encountered an error</h2>

          {/* Description */}
          <p className="text-muted-foreground mb-6 max-w-sm typo-caption">
            Something went wrong while rendering this view. You can try reloading it, or switch to a
            different tab.
          </p>

          {/* Error detail (dev only) */}
          {this.state.error && (
            <pre className="mb-6 px-4 py-3 rounded-lg bg-muted max-w-md w-full overflow-x-auto text-left text-muted-foreground typo-mono">
              {this.state.error.message}
            </pre>
          )}

          {/* Retry button */}
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
