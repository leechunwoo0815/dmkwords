// 认证上下文：登录 / 登出 / 启动时校验 token 并拉取权限
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { apiLogin, apiMe, type AdminUser } from "./api/admin";
import { clearToken, getToken, setToken } from "./api/client";

interface AuthState {
  user: AdminUser | null;
  permissions: string[];
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    apiMe()
      .then((me) => {
        setUser(me.user);
        setPermissions(me.permissions);
      })
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await apiLogin({ username, password });
    setToken(result.token);
    setUser(result.user);
    const me = await apiMe();
    setPermissions(me.permissions);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    setPermissions([]);
    window.location.href = "/login";
  }, []);

  return (
    <AuthContext.Provider value={{ user, permissions, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}

export function hasPermission(permissions: string[], code: string): boolean {
  return permissions.includes("*") || permissions.includes(code);
}
