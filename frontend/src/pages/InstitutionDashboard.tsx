import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import {
  fetchInstitutionDashboard,
  type Bucket,
  type InstitutionCoverage,
  type DataHealthScore,
} from "../api/dashboard";
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

function CoverageMeterSection({ coverage }: { coverage?: InstitutionCoverage }) {
  if (!coverage) return null;
  const bandBg =
    coverage.status_band === "GREEN"
      ? "bg-emerald-500"
      : coverage.status_band === "AMBER"
      ? "bg-amber-500"
      : "bg-rose-500";
  const badgeClass =
    coverage.status_band === "GREEN"
      ? "bg-emerald-100 text-emerald-800 border-emerald-300"
      : coverage.status_band === "AMBER"
      ? "bg-amber-100 text-amber-800 border-amber-300"
      : "bg-rose-100 text-rose-800 border-rose-300";

  return (
    <Card title="Headcount Coverage Meter (FR-21)" note="Declared headcount vs student records received">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-slate-900">{coverage.coverage_percentage}%</span>
            <span className="text-xs text-slate-500">
              ({coverage.received_records.toLocaleString()} of {coverage.declared_headcount.toLocaleString()} declared)
            </span>
          </div>
          <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold border ${badgeClass}`}>
            {coverage.status_band} BAND
          </span>
        </div>

        <div className="w-full bg-slate-100 rounded-full h-3 overflow-hidden">
          <div
            className={`h-3 rounded-full ${bandBg}`}
            style={{ width: `${Math.min(100, coverage.coverage_percentage)}%` }}
          />
        </div>

        {coverage.programmes.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1.5 pr-2">Programme</th>
                  <th className="py-1.5 pr-2 text-center">Shift</th>
                  <th className="py-1.5 pr-2 text-right">Declared</th>
                  <th className="py-1.5 pr-2 text-right">Received</th>
                  <th className="py-1.5 pr-2 text-right">Coverage</th>
                  <th className="py-1.5 pr-2 text-center">Band</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {coverage.programmes.map((p) => (
                  <tr key={p.institution_programme_id}>
                    <td className="py-1.5 pr-2 font-medium">
                      {p.programme_name} ({p.programme_code})
                    </td>
                    <td className="py-1.5 pr-2 text-center font-mono">{p.shift}</td>
                    <td className="py-1.5 pr-2 text-right">{p.declared_enrolment}</td>
                    <td className="py-1.5 pr-2 text-right">{p.received_records}</td>
                    <td className="py-1.5 pr-2 text-right font-semibold">{p.coverage_percentage}%</td>
                    <td className="py-1.5 pr-2 text-center">
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.status_band === "GREEN"
                            ? "bg-emerald-100 text-emerald-800"
                            : p.status_band === "AMBER"
                            ? "bg-amber-100 text-amber-800"
                            : "bg-rose-100 text-rose-800"
                        }`}
                      >
                        {p.status_band}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

function DataHealthScoreSection({ health }: { health?: DataHealthScore }) {
  if (!health) return null;
  const ratingBadge =
    health.rating === "EXCELLENT"
      ? "bg-emerald-100 text-emerald-800 border-emerald-300"
      : health.rating === "GOOD"
      ? "bg-blue-100 text-blue-800 border-blue-300"
      : health.rating === "NEEDS_ATTENTION"
      ? "bg-amber-100 text-amber-800 border-amber-300"
      : "bg-rose-100 text-rose-800 border-rose-300";

  return (
    <Card title="Data Health Score (FR-23)" note="Data completeness, validity, timeliness & clean share (0-100)">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-extrabold text-slate-900">{health.overall_score}</span>
            <span className="text-sm font-medium text-slate-500">/ 100</span>
          </div>
          <span className={`px-3 py-1 rounded-full text-xs font-bold border ${ratingBadge}`}>
            {health.rating.replace(/_/g, " ")}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="p-2.5 rounded bg-slate-50 border border-slate-200">
            <div className="flex justify-between font-medium text-slate-700">
              <span>Completeness</span>
              <span>{health.components.completeness.score} / 25</span>
            </div>
            <div className="mt-1.5 w-full bg-slate-200 rounded-full h-1.5">
              <div
                className="bg-indigo-600 h-1.5 rounded-full"
                style={{ width: `${health.components.completeness.pct}%` }}
              />
            </div>
          </div>

          <div className="p-2.5 rounded bg-slate-50 border border-slate-200">
            <div className="flex justify-between font-medium text-slate-700">
              <span>Validity</span>
              <span>{health.components.validity.score} / 25</span>
            </div>
            <div className="mt-1.5 w-full bg-slate-200 rounded-full h-1.5">
              <div
                className="bg-emerald-600 h-1.5 rounded-full"
                style={{ width: `${health.components.validity.pct}%` }}
              />
            </div>
          </div>

          <div className="p-2.5 rounded bg-slate-50 border border-slate-200">
            <div className="flex justify-between font-medium text-slate-700">
              <span>Timeliness</span>
              <span>{health.components.timeliness.score} / 25</span>
            </div>
            <div className="mt-1.5 w-full bg-slate-200 rounded-full h-1.5">
              <div
                className="bg-blue-600 h-1.5 rounded-full"
                style={{ width: `${health.components.timeliness.pct}%` }}
              />
            </div>
          </div>

          <div className="p-2.5 rounded bg-slate-50 border border-slate-200">
            <div className="flex justify-between font-medium text-slate-700">
              <span>Clean Share</span>
              <span>{health.components.clean_share.score} / 25</span>
            </div>
            <div className="mt-1.5 w-full bg-slate-200 rounded-full h-1.5">
              <div
                className="bg-purple-600 h-1.5 rounded-full"
                style={{ width: `${health.components.clean_share.pct}%` }}
              />
            </div>
          </div>
        </div>

        <p className="text-xs text-slate-500 mt-1">{health.summary_en}</p>
      </div>
    </Card>
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
                  <dt className="text-xs text-slate-500">{t("dash.awaitingAicte")}</dt>
                  <dd className="text-xl font-semibold text-slate-700">
                    {q.data.over_cap.awaiting_aicte_review}
                  </dd>
                </div>
              </dl>

              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-500">
                      <th className="pb-2 pr-3">{t("dash.bucket")}</th>
                      <th className="pb-2 pr-3">{t("dash.key")}</th>
                      <th className="pb-2 pr-3">{t("dash.usage")}</th>
                      <th className="pb-2 pr-3 text-right">{t("dash.used")}</th>
                      <th className="pb-2 pr-3 text-right">{t("dash.cap")}</th>
                      <th className="pb-2 pr-3 text-right">{t("dash.status")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="bg-slate-50 font-medium">
                      <td colSpan={6} className="py-1.5">
                        {t("dash.overall")}
                      </td>
                    </tr>
                    {q.data.seats.totals.map((b) => (
                      <BucketRow
                        key={b.ledger_key}
                        b={b}
                        label={t(`buckets.${b.ledger_key}`)}
                      />
                    ))}
                    {q.data.seats.programmes.map((p) => (
                      <Fragment key={p.institution_programme_id}>
                        <tr className="bg-slate-50 font-medium">
                          <td colSpan={6} className="py-1.5 pt-3">
                            {p.programme_name} ({p.programme_code}) · shift {p.shift} · intake{" "}
                            {p.approved_intake} · declared {p.declared_enrolment ?? "-"} · received{" "}
                            {p.records_received}
                          </td>
                        </tr>
                        {p.buckets.map((b) => (
                          <BucketRow
                            key={b.ledger_key}
                            b={b}
                            label={t(`buckets.${b.ledger_key}`)}
                          />
                        ))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <CoverageMeterSection coverage={q.data.coverage_meter} />
          <DataHealthScoreSection health={q.data.data_health_score} />

          <Card title={t("dash.tiers")} note={q.data.trust_tiers.summary_en}>
            <div className="flex h-4 w-full overflow-hidden rounded bg-slate-100">
              {(["T3", "T2", "T1", "T0"] as const).map((tier) => {
                const count = q.data.trust_tiers.by_tier[tier];
                const pct = q.data.trust_tiers.total
                  ? (count / q.data.trust_tiers.total) * 100
                  : 0;
                if (!count) return null;
                return (
                  <div
                    key={tier}
                    style={{ width: `${pct}%` }}
                    className={`${TIER_STYLE[tier]} transition-all`}
                    title={`${tier}: ${count}`}
                  />
                );
              })}
            </div>
            <ul className="mt-3 flex flex-wrap gap-4 text-xs">
              {(["T0", "T1", "T2", "T3"] as const).map((tier) => (
                <li key={tier} className="flex items-center gap-1.5">
                  <span className={`inline-block h-2.5 w-2.5 rounded-full ${TIER_STYLE[tier]}`} />
                  <span className="font-mono">{tier}</span>
                  <span className="text-slate-500">
                    {TIER_LABEL[tier]}: {q.data.trust_tiers.by_tier[tier]}
                  </span>
                </li>
              ))}
            </ul>
          </Card>

          <Card title={t("dash.submissions")}>
            <ul className="flex flex-wrap gap-4 text-sm">
              {Object.entries(q.data.submissions.by_status).map(([k, v]) => (
                <li key={k} className="rounded border border-slate-200 px-3 py-1.5">
                  <div className="text-xs text-slate-500">{k.replace(/_/g, " ")}</div>
                  <div className="text-lg font-semibold">{v}</div>
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
        </div>
      )}
    </AppShell>
  );
}
