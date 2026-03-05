import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AuthContext } from "@/hooks/auth-context";
import { MOCK_USER } from "@/hooks/auth-mock-user";
import { getAccessToken } from "@/lib/auth/tokenStore";
import { authEndpoints } from "@/lib/rlm-api/auth";
import type {
  AuthContextValue,
  PlanTier,
  UserProfile,
} from "@/hooks/auth-types";

interface AuthProviderProps {
  children: ReactNode;
}

function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<UserProfile | null>(null);

  useEffect(() => {
    if (!getAccessToken()) return;

    let cancelled = false;
    void authEndpoints
      .me()
      .then((me) => {
        if (cancelled) return;
        setUser({
          id: me.user_id ?? me.user_claim ?? MOCK_USER.id,
          name: me.name ?? "Authenticated User",
          email: me.email ?? "",
          initials: (me.name ?? "AU")
            .split(" ")
            .map((segment) => segment[0] ?? "")
            .join("")
            .slice(0, 2)
            .toUpperCase(),
          role: "Member",
          plan: "free",
          org: me.tenant_id ?? me.tenant_claim ?? "Default",
        });
      })
      .catch(() => {
        if (cancelled) return;
        authEndpoints.clearLocalAuth();
        setUser(null);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (_email: string, _password: string): Promise<boolean> => {
      try {
        await authEndpoints.login();
        const me = await authEndpoints.me();
        setUser({
          id: me.user_id ?? me.user_claim ?? MOCK_USER.id,
          name: me.name ?? "Authenticated User",
          email: me.email ?? "",
          initials: (me.name ?? "AU")
            .split(" ")
            .map((segment) => segment[0] ?? "")
            .join("")
            .slice(0, 2)
            .toUpperCase(),
          role: "Member",
          plan: "free",
          org: me.tenant_id ?? me.tenant_claim ?? "Default",
        });
        return true;
      } catch {
        setUser(null);
        return false;
      }
    },
    [],
  );

  const logout = useCallback(() => {
    void authEndpoints.logout().catch(() => undefined);
    setUser(null);
  }, []);

  const setPlan = useCallback((plan: PlanTier) => {
    setUser((prev) => (prev ? { ...prev, plan } : null));
  }, []);

  const value: AuthContextValue = {
    isAuthenticated: user !== null,
    user,
    login,
    logout,
    setPlan,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export { AuthProvider };
