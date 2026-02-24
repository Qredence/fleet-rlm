import { useCallback, useState, type ReactNode } from "react";

import { AuthContext } from "@/hooks/auth-context";
import { MOCK_USER } from "@/hooks/auth-mock-user";
import type {
  AuthContextValue,
  PlanTier,
  UserProfile,
} from "@/hooks/auth-types";

interface AuthProviderProps {
  children: ReactNode;
}

function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<UserProfile | null>(MOCK_USER);

  const login = useCallback(
    async (email: string, _password: string): Promise<boolean> => {
      await new Promise((resolve) => setTimeout(resolve, 800));
      setUser({ ...MOCK_USER, email });
      return true;
    },
    [],
  );

  const logout = useCallback(() => {
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
