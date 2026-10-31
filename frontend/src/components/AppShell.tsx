import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";
import { AREA_NAV, AREA_TITLE, type Area } from "../lib/roles";

export function AppShell({ area, children }: { area: Area; children: ReactNode }) {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

  const nav = AREA_NAV[area].filter((item) => !item.roles || item.roles.includes(user.role));

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-semibold text-brand-900">{t("app.name")}</span>
          <span className="text-sm text-slate-500">{AREA_TITLE[area]}</span>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <div className="text-right">
            <div className="font-medium">{user.full_name ?? user.email}</div>
            <div className="text-slate-500">
              <span className="rounded bg-brand-50 px-1.5 py-0.5 text-xs font-medium text-brand-700">
                {t(`role.${user.role}`)}
              </span>
              {user.institution && <span className="ml-2">{user.institution.name}</span>}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              signOut();
              navigate("/login", { replace: true });
            }}
            className="rounded border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
          >
            {t("shell.signOut")}
          </button>
        </div>
      </header>

      <div className="flex flex-1">
        <nav aria-label="Portal sections" className="w-60 border-r border-slate-200 bg-white p-4">
          <ul className="space-y-1">
            <li>
              <NavLink
                end
                to={`/${area}`}
                className={({ isActive }) =>
                  `block rounded px-3 py-2 text-sm ${
                    isActive ? "bg-brand-50 font-medium text-brand-700" : "hover:bg-slate-100"
                  }`
                }
              >
                Home
              </NavLink>
            </li>
            {nav.map((item) => (
              <li key={item.label}>
                {item.path ? (
                  <NavLink
                    to={item.path}
                    className={({ isActive }) =>
                      `block rounded px-3 py-2 text-sm ${
                        isActive ? "bg-brand-50 font-medium text-brand-700" : "hover:bg-slate-100"
                      }`
                    }
                  >
                    {item.label}
                  </NavLink>
                ) : (
                  <span
                    aria-disabled="true"
                    title={`${t("shell.comingSoon")} (phase ${item.phase})`}
                    className="flex items-center justify-between rounded px-3 py-2 text-sm text-slate-400"
                  >
                    {item.label}
                    <span className="text-xs">P{item.phase}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </nav>
        <main className="flex-1 p-8">{children}</main>
      </div>
    </div>
  );
}
