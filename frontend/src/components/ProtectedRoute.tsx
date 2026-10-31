import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";
import { homePath, type Role } from "../lib/roles";

/**
 * Client-side guard for UX only. The API enforces RBAC server-side on every route;
 * this just keeps users out of portals that would only show them 403s.
 */
export function ProtectedRoute({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="p-8 text-slate-500">Loading…</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (!roles.includes(user.role)) {
    return (
      <div className="mx-auto max-w-md p-8 text-center">
        <p className="mb-4 text-slate-700">{t("errors.forbidden")}</p>
        <a className="text-brand-600 underline" href={homePath(user.role)}>
          Go to your portal
        </a>
      </div>
    );
  }
  return <>{children}</>;
}
