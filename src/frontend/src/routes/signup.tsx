import { createFileRoute, useNavigate, Link, redirect } from "@tanstack/react-router";
import { useEffect } from "react";
import { SignUpForm } from "@neondatabase/auth-ui";

import { AuthLayout } from "@/components/product";
import { useAuth } from "@/lib/auth/auth-context";
import { isNeonAuthConfigured } from "@/lib/auth/neon";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/signup")({
  beforeLoad: () => {
    if (!isNeonAuthConfigured()) {
      throw redirect({ to: "/app/workspace", replace: true });
    }
  },
  component: SignupScreen,
  errorComponent: RouteErrorScreen,
});

function SignupScreen() {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();

  useEffect(() => {
    if (isAuthenticated) {
      navigate({ to: "/app/workspace", replace: true });
    }
  }, [isAuthenticated, navigate]);

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Sign up to access your RLM workspace"
      footer={
        <div className="text-center">
          <Link
            to="/login"
            className="text-muted-foreground transition-colors hover:text-foreground typo-caption"
          >
            Already have an account? Sign in
          </Link>
        </div>
      }
    >
      <SignUpForm
        className="w-full"
        classNames={{
          base: "border-0 bg-transparent p-0 shadow-none w-full !max-w-none",
        }}
        localization={{}}
      />
    </AuthLayout>
  );
}
