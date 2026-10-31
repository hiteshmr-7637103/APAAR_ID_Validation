import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";
import { homePath, type Role } from "../lib/roles";

// Seeded demo accounts (backend/app/db/seed.py). Shown to make the demo easy to drive.
const DEMO_ACCOUNTS: { email: string; role: Role }[] = [
  { email: "admin@aicte.demo", role: "AICTE_ADMIN" },
  { email: "reviewer@aicte.demo", role: "AICTE_REVIEWER" },
  { email: "maker@inst1.demo", role: "INST_MAKER" },
  { email: "checker@inst1.demo", role: "INST_CHECKER" },
  { email: "verifier@employer.demo", role: "VERIFIER" },
  { email: "student@student.demo", role: "STUDENT" },
];
const DEMO_PASSWORD = "Demo@2026";

export function LoginPage() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) return <Navigate to={homePath(user.role)} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const u = await signIn(email, password);
      navigate(homePath(u.role), { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.network"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="grid w-full max-w-4xl gap-8 md:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
          <h1 className="text-2xl font-semibold text-brand-900">{t("app.name")}</h1>
          <p className="mb-6 mt-1 text-sm text-slate-500">{t("app.tagline")}</p>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <h2 className="text-lg font-medium">{t("login.title")}</h2>
            <label className="block">
              <span className="text-sm font-medium text-slate-700">{t("login.email")}</span>
              <input
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-slate-700">{t("login.password")}</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
              />
            </label>
            {error && (
              <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={submitting || !email || !password}
              className="w-full rounded bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {submitting ? t("login.submitting") : t("login.submit")}
            </button>
          </form>
        </div>

        <div className="rounded-lg border border-dashed border-slate-300 bg-white/60 p-6">
          <h2 className="font-medium">{t("login.demoAccounts")}</h2>
          <p className="mb-4 mt-1 text-sm text-slate-500">
            {t("login.demoHint")} <code className="rounded bg-slate-100 px-1">{DEMO_PASSWORD}</code>
          </p>
          <ul className="space-y-2">
            {DEMO_ACCOUNTS.map((a) => (
              <li key={a.email}>
                <button
                  type="button"
                  onClick={() => {
                    setEmail(a.email);
                    setPassword(DEMO_PASSWORD);
                  }}
                  className="flex w-full items-center justify-between rounded border border-slate-200 bg-white px-3 py-2 text-left text-sm hover:border-brand-600"
                >
                  <span>{a.email}</span>
                  <span className="text-xs text-slate-500">{t(`role.${a.role}`)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
