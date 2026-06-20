import { createContext, useContext } from "react";

import type { AuthContextValue } from "@/lib/auth/types";

const noopVoid = () => {};

const defaultAuthCtx: AuthContextValue = {
  isAuthenticated: false,
  user: null,
  logout: noopVoid,
  setPlan: noopVoid,
  refresh: async () => {},
};

const AuthContext = createContext<AuthContextValue>(defaultAuthCtx);

function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

export { AuthContext, useAuth };
