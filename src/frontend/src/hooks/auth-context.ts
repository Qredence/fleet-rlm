import { createContext, useContext } from "react";

import type { AuthContextValue } from "@/hooks/auth-types";

const noopAsync = async () => false as boolean;
const noopVoid = () => {};

const defaultAuthCtx: AuthContextValue = {
  isAuthenticated: false,
  user: null,
  login: noopAsync,
  logout: noopVoid,
  setPlan: noopVoid,
};

const AuthContext = createContext<AuthContextValue>(defaultAuthCtx);

function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

export { AuthContext, useAuth };
