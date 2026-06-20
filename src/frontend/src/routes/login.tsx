import { createFileRoute, useNavigate, redirect } from "@tanstack/react-router";
import { useEffect } from "react";
import { Link } from "@tanstack/react-router";
import { SignInForm } from "@neondatabase/auth-ui";

import { AuthLayout } from "@/components/product";
import { useAuth } from "@/lib/auth/auth-context";
import { isNeonAuthConfigured } from "@/lib/auth/neon";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/login")({
  beforeLoad: () => {
    if (!isNeonAuthConfigured()) {
      throw redirect({ to: "/app/workspace", replace: true });
    }
  },
  component: LoginScreen,
  errorComponent: RouteErrorScreen,
});

function LoginScreen() {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();

  useEffect(() => {
    if (isAuthenticated) {
      navigate({ to: "/app/workspace", replace: true });
    }
  }, [isAuthenticated, navigate]);

  return (
    <AuthLayout
      title="Sign in to Fleet RLM"
      subtitle="Sign in or sign up to access your RLM workspace"
      footer={
        <div className="text-center">
          <Link
            to="/signup"
            className="text-muted-foreground transition-colors hover:text-foreground typo-caption"
          >
            Need an account? Sign up
          </Link>
        </div>
      }
    >
      <SignInForm
        className="w-full"
        classNames={{
          base: "border-0 bg-transparent p-0 shadow-none w-full !max-w-none",
        }}
        localization={{}}
      />
    </AuthLayout>
  );
}
