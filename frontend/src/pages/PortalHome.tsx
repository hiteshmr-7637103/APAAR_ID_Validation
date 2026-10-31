import { Link } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";
import { AREA_NAV, AREA_TITLE, type Area } from "../lib/roles";

/** Landing page for each portal: who you are, available screens and what is coming. */
export function PortalHome({ area }: { area: Area }) {
  const { user } = useAuth();
  if (!user) return null;
  const items = AREA_NAV[area].filter((i) => !i.roles || i.roles.includes(user.role));

  return (
    <AppShell area={area}>
      <h1 className="text-2xl font-semibold">{AREA_TITLE[area]}</h1>
      <p className="mt-1 text-slate-500">{t("shell.phaseNote")}</p>

      <dl className="mt-6 grid max-w-2xl grid-cols-[max-content_1fr] gap-x-6 gap-y-2 rounded-lg border border-slate-200 bg-white p-6 text-sm">
        <dt className="text-slate-500">Signed in as</dt>
        <dd>{user.email}</dd>
        <dt className="text-slate-500">Role</dt>
        <dd>{t(`role.${user.role}`)}</dd>
        {user.institution && (
          <>
            <dt className="text-slate-500">Institution</dt>
            <dd>
              {user.institution.name} ({user.institution.code})
            </dd>
          </>
        )}
        {user.student_apaar_id && (
          <>
            <dt className="text-slate-500">APAAR ID</dt>
            <dd className="font-mono">{user.student_apaar_id}</dd>
          </>
        )}
      </dl>

      <h2 className="mt-8 text-lg font-medium">Screens</h2>
      <ul className="mt-3 grid max-w-3xl gap-3 sm:grid-cols-2">
        {items.map((item) => (
          <li
            key={item.label}
            className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm"
          >
            <div className="font-medium">{item.label}</div>
            {item.path ? (
              <Link to={item.path} className="text-brand-600 underline">
                {t("shell.open")}
              </Link>
            ) : (
              <div className="text-slate-500">
                {t("shell.comingSoon")} (phase {item.phase})
              </div>
            )}
          </li>
        ))}
      </ul>
    </AppShell>
  );
}
