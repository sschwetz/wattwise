import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";
import { configureApiClient } from "../api/client";

const STORAGE_KEY = "access_token";

interface AuthContextValue {
  accessToken: string | null;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [accessToken, setAccessToken] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));

  const login = useCallback((token: string) => {
    localStorage.setItem(STORAGE_KEY, token);
    setAccessToken(token);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setAccessToken(null);
  }, []);

  // Wired during render, deliberately not in an effect: passive effects fire
  // child-before-parent, so an effect here would run only after a descendant's
  // first query had already issued an unauthenticated request whose 401 the
  // still-unconfigured handler would swallow. configureApiClient only reassigns
  // module-level function references, so calling it on every render is safe.
  configureApiClient(() => localStorage.getItem(STORAGE_KEY), logout);

  const value = useMemo(() => ({ accessToken, login, logout }), [accessToken, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
