import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchMe, login as apiLogin, logout as apiLogout, type User } from "../api/auth";
import { tokenStore } from "../api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    enabled: !!tokenStore.access,
    retry: false,
    staleTime: 60_000,
  });

  const signIn = useCallback(
    async (email: string, password: string) => {
      const user = await apiLogin(email, password);
      qc.setQueryData(["me"], user);
      return user;
    },
    [qc],
  );

  const signOut = useCallback(() => {
    apiLogout();
    qc.setQueryData(["me"], null);
    qc.clear();
  }, [qc]);

  const value = useMemo<AuthState>(
    () => ({
      user: me.data ?? null,
      loading: !!tokenStore.access && me.isLoading,
      signIn,
      signOut,
    }),
    [me.data, me.isLoading, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
