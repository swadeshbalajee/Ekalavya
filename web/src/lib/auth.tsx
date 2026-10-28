import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import type { User } from "./types";

type AuthState = {
  user: User | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, displayName: string) => Promise<void>;
  logout: () => Promise<void>;
  update: (changes: Partial<User>) => Promise<User>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const client = useQueryClient();

  const signedOut = useCallback(() => {
    setToken(null);
    setUser(null);
    client.clear();
  }, [client]);

  useEffect(() => {
    setUnauthorizedHandler(signedOut);
    if (!getToken()) {
      setReady(true);
      return;
    }
    api<User>("/v1/auth/me")
      .then(setUser)
      .catch(() => signedOut())
      .finally(() => setReady(true));
  }, [signedOut]);

  const accept = (response: { token: string; user: User }) => {
    setToken(response.token);
    client.clear();
    setUser(response.user);
  };

  const value: AuthState = {
    user,
    ready,
    login: async (username, password) =>
      accept(await api("/v1/auth/login", { body: { username, password } })),
    register: async (username, password, displayName) =>
      accept(
        await api("/v1/auth/register", {
          body: { username, password, display_name: displayName || username },
        }),
      ),
    logout: async () => {
      try {
        await api("/v1/auth/logout", { method: "POST" });
      } catch {
        /* the session is dropped locally either way */
      }
      signedOut();
    },
    update: async (changes) => {
      const updated = await api<User>("/v1/auth/me", { method: "PATCH", body: changes });
      setUser(updated);
      return updated;
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}
