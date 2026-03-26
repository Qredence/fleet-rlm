import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AuthContext } from "@/lib/auth/auth-context";
import { MOCK_USER } from "@/lib/auth/auth-mock-user";
import { getAccessToken } from "@/lib/auth/tokenStore";
import {
  initializeEntraSession,
  isEntraAuthConfigured,
  loginWithEntra,
  logoutWithEntra,
} from "@/lib/auth/entra";
import { authEndpoints } from "@/lib/rlm-api/auth";
import type { AuthContextValue, PlanTier, UserProfile } from "@/lib/auth/types";

interface AuthProviderProps {
  children: ReactNode;
}

function mapProfile(
  me: Awaited<ReturnType<typeof authEndpoints.me>>,
): UserProfile {
  return {
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
    org: me.tenant_claim ?? me.tenant_id ?? "Default",
  };
}

function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<UserProfile | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (isEntraAuthConfigured()) {
          await initializeEntraSession();
        }
        if (!getAccessToken()) {
          if (!cancelled) setUser(null);
          return;
        }
        const me = await authEndpoints.me();
        if (cancelled) return;
        setUser(mapProfile(me));
      } catch {
        if (cancelled) return;
        authEndpoints.clearLocalAuth();
        setUser(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (): Promise<boolean> => {
    try {
      if (!isEntraAuthConfigured()) {
        return false;
      }
      await loginWithEntra();
      const me = await authEndpoints.me();
      setUser(mapProfile(me));
      return true;
    } catch {
      setUser(null);
      authEndpoints.clearLocalAuth();
      return false;
    }
  }, []);

  const logout = useCallback(() => {
    void logoutWithEntra().catch(() => undefined);
    authEndpoints.clearLocalAuth();
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
