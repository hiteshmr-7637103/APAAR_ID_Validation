import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { fetchInstitutionDashboard, type Bucket } from "../api/dashboard";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";

const TIER_STYLE: Record<string, string> = {
  T0: "bg-slate-400",
  T1: "bg-sky-500",
  T2: "bg-brand-600",
  T3: "bg-emerald-600",
};
const TIER_LABEL: Record<string, string> = {
  T0: "T0 submitted",
  T1: "T1 rules passed",
  T2: "T2 registry matched",
  T3: "T3 student confirmed",
};

function Card({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="text-lg font-medium">{title}</h2>
      {note && <p className="mt-1 text-xs text-slate-500">{note}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function UsageBar({ b }: { b: Bucket }) {
  const pct = b.cap > 0 ? Math.min(100, Math.round((b.used / b.cap) * 100)) : b.used > 0 ? 100 : 0;
  const over = b.excess > 0;
  return (
    <div
      className="h-2 w-full overflow-hidden rounded bg-slate-100"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={b.cap}
      aria-valuenow={b.used}
      aria-label={`${b.ledger_key}: ${b.used} of ${b.cap}`}
    >
      <div
        className={`h-2 ${over ? "bg-amber-500" : "bg-brand-600"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function BucketRow({ b, label }: { b: Bucket; label: ReactNode }) {
  return (
    <tr className={`border-t border-slate-100 ${b.excess > 0 ? "bg-amber-50 text-amber-900" : ""}`}>
      <td className="py-1 pr-3">{label}</td>
      <td className="py-1 pr-3 font-mono text-xs">{b.ledger_key}</td>
      <td className="w-40 py-1 pr-3">
        <UsageBar b={b} />
      </td>
      <td className="py-1 pr-3 text-right">{b.used}</td>
      <td className="py-1 pr-3 text-right">{b.cap}</td>
      <td className="py-1 pr-3 text-right font-medium">
        {b.excess > 0 ? `+${b.excess} flagged` : "-"}
      </td>
    </tr>
  );
}

export function InstitutionDashboardPage() {
  const { user } = useAuth();
  const instId = user?.institution?.id;
  const q = useQuery({
    queryKey: ["institution-dashboard", instId],
    queryFn: () => fetchInstitutionDashboard(instId!),
    enabled: !!instId,
  });

  return (
    <AppShell area="institution">
      <h1 className="text-2xl font-semibold">{t("dash.title")}</h1>
      {q.data && (
        <p className="mt-1 text-sm text-slate-500">
          {q.data.institution.name} ({q.data.institution.code}) · academic year{" "}
          {q.data.academic_year}
        </p>
      )}
      {q.isLoading && <p className="mt-4 text-sm text-slate-500">Loading…</p>}
      {q.error && <p className="mt-4 text-sm text-red-700">{t("errors.network")}</p>}

      {q.data && (
        <div className="mt-6 grid gap-5 xl:grid-cols-2">
          <div className="xl:col-span-2">
            <Card title={t("dash.seats")} note={t("dash.seatsNote")}>
              <dl className="mb-4 flex flex-wrap gap-4 text-sm">
                <div className="rounded border border-slate-200 px-3 py-2">
                  <dt className="text-xs text-slate-500">{t("dash.overCap")}</dt>
                  <dd className="text-xl font-semibold text-amber-700">
                    {q.data.over_cap.flagged_records}
                  </dd>
                </div>
                <div className="rounded border border-slate-200 px-3 py-2">
                  <dt className="text-xs text-slate-500">Held for AICTE review</dt>
                  <dd className="text-xl font-semibold text-amber-700">
                    {q.data.over_cap.awaiting_aicte_review}
                  </dd>
                </div>
              </dl>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr>
                      <th className="py-1 pr-3">Programme</th>
                      <th className="py-1 pr-3">Bucket</th>
                      <th className="py-1 pr-3">Utilisation</th>
                      <th className="py-1 pr-3 text-right">Used</th>
                      <th className="py-1 pr-3 text-right">Cap</th>
                      <th className="py-1 pr-3 text-right">Excess</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.seats.programmes.map((p) => (
                      <Fragment key={p.institution_programme_id}>
                        {p.buckets
                          .filter((b) => b.cap > 0 || b.used > 0)
                          .map((b) => (
                            <BucketRow
                              key={`${p.institution_programme_id}-${b.ledger_key}`}
                              b={b}
                              label={
                                <>
                                  {p.programme_code}{" "}
                                  <span className="text-xs text-slate-500">({p.shift})</span>
                                </>
                              }
                            />
                          ))}
                      </Fragment>
                    ))}
                    {q.data.seats.totals
                      .filter((b) => b.cap > 0 || b.used > 0)
                      .map((b) => (
                        <BucketRow
                          key={`total-${b.ledger_key}`}
                          b={b}
                          label={<strong>All programmes</strong>}
                        />
                      ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card title={t("dash.tiers")} note={t("dash.tiersNote")}>
            <div
              className="flex h-4 w-full overflow-hidden rounded bg-slate-100"
              aria-hidden="true"
            >
              {Object.entries(q.data.trust_tiers.by_tier).map(([tier, n]) =>
                n > 0 ? (
                  <div
                    key={tier}
                    className={TIER_STYLE[tier]}
                    style={{ width: `${(n / Math.max(1, q.data.trust_tiers.total)) * 100}%` }}
                  />
                ) : null,
              )}
            </div>
            <ul className="mt-3 grid grid-cols-2 gap-2 text-sm">
              {Object.entries(q.data.trust_tiers.by_tier).map(([tier, n]) => (
                <li key={tier} className="flex items-center gap-2">
                  <span className={`inline-block h-3 w-3 rounded-sm ${TIER_STYLE[tier]}`} />
                  {TIER_LABEL[tier]}: <strong>{n}</strong>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500">{q.data.trust_tiers.summary_en}</p>
          </Card>

          <Card title={t("dash.submissions")}>
            <dl className="flex flex-wrap gap-3 text-sm">
              {Object.entries(q.data.submissions.by_status).map(([k, v]) => (
                <div key={k} className="rounded border border-slate-200 px-3 py-1">
                  <dt className="text-xs text-slate-500">{k}</dt>
                  <dd className="font-semibold">{v}</dd>
                </div>
              ))}
            </dl>
            <ul className="mt-3 divide-y divide-slate-100 text-sm">
              {q.data.submissions.recent.map((b) => (
                <li key={b.id} className="flex justify-between py-1">
                  <span>
                    #{b.id} {b.filename ?? b.source} · {b.records} records
                    {b.over_cap_records > 0 && ` · ${b.over_cap_records} flagged`}
                  </span>
                  <span className="text-xs text-slate-500">{b.stage}</span>
                </li>
              ))}
            </ul>
            {user?.role === "INST_CHECKER" && q.data.submissions.pending_approval > 0 && (
              <Link
                to="/institution/approvals"
                className="mt-2 inline-block text-sm text-brand-700 underline"
              >
                {q.data.submissions.pending_approval} batch(es) awaiting your approval
              </Link>
            )}
            {user?.role === "INST_MAKER" && (
              <Link
                to="/institution/uploads"
                className="mt-2 inline-block text-sm text-brand-700 underline"
              >
                {t("upload.title")}
              </Link>
            )}
            <p className="mt-3 text-xs text-slate-500">
              Records:{" "}
              {Object.entries(q.data.records_by_status)
                .filter(([, v]) => v > 0)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            </p>
          </Card>

          <Card title={t("dash.errors")}>
            {q.data.recent_errors.length === 0 ? (
              <p className="text-sm text-slate-500">{t("dash.noErrors")}</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {q.data.recent_errors.map((e, i) => (
                  <li
                    key={i}
                    className="rounded border border-red-200 bg-red-50 px-3 py-2 text-red-800"
                  >
                    <span className="mr-2 font-mono text-xs">{e.code}</span>
                    Batch #{e.batch_id}
                    {e.row != null && `, row ${e.row}`}: {e.message_en}
                    {e.suggested_fix?.hint && (
                      <div className="text-xs opacity-90">{e.suggested_fix.hint}</div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title={t("dash.cases")}>
            {q.data.pending_cases.length === 0 ? (
              <p className="text-sm text-slate-500">{t("dash.noCases")}</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {q.data.pending_cases.map((c) => (
                  <li key={c.id} className="rounded border border-slate-200 px-3 py-2">
                    <span className="mr-2 font-mono text-xs">{c.type}</span>#{c.id}{" "}
                    {c.summary_en && <span className="text-slate-600">{c.summary_en}</span>}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <div className="xl:col-span-2">
            <p className="text-xs text-slate-500">
              {t("dash.deferred")}:{" "}
              {Object.entries(q.data.deferred)
                .map(([k, v]) => `${k.replace(/_/g, " ")} (${v})`)
                .join(", ")}
            </p>
          </div>
        </div>
      )}
    </AppShell>
  );
}
