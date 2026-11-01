import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { fetchAicteDashboard, type AicteDashboard } from "../api/dashboard";
import { tokenStore } from "../api/client";

const STATES = [
  "ALL",
  "Delhi",
  "Maharashtra",
  "Karnataka",
  "Tamil Nadu",
  "Uttar Pradesh",
  "Gujarat",
  "Telangana",
  "West Bengal",
];

const ACADEMIC_YEARS = ["2024-2025", "2025-2026"];

export function AicteDashboardPage() {
  const [data, setData] = useState<AicteDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [academicYear, setAcademicYear] = useState("2024-2025");
  const [selectedState, setSelectedState] = useState("ALL");
  const [sseConnected, setSseConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());

  const loadData = useCallback(async () => {
    try {
      const stateParam = selectedState === "ALL" ? undefined : selectedState;
      const res = await fetchAicteDashboard(academicYear, stateParam);
      setData(res);
      setError(null);
      setLastUpdated(new Date());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [academicYear, selectedState]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Connect to SSE stream for live dashboard updates (FR-22)
  useEffect(() => {
    const token = tokenStore.access;
    const url = `/api/v1/dashboards/events/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    const eventSource = new EventSource(url);

    eventSource.onopen = () => {
      setSseConnected(true);
    };

    eventSource.addEventListener("ping", () => {
      setSseConnected(true);
    });

    eventSource.addEventListener("batch_decided", () => {
      loadData();
    });

    eventSource.addEventListener("case_updated", () => {
      loadData();
    });

    eventSource.onerror = () => {
      setSseConnected(false);
    };

    return () => {
      eventSource.close();
    };
  }, [loadData]);

  const coverageBandColor = (band: "RED" | "AMBER" | "GREEN") => {
    if (band === "GREEN") return "bg-emerald-500 text-white";
    if (band === "AMBER") return "bg-amber-500 text-white";
    return "bg-rose-500 text-white";
  };

  return (
    <AppShell area="aicte">
      <div className="space-y-6">
        {/* Header & Controls */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-6 rounded-lg shadow-sm border border-slate-200">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-slate-900">AICTE National Apex Dashboard</h1>
              {sseConnected ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  Live SSE Connected
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-600 border border-slate-300">
                  <span className="w-2 h-2 rounded-full bg-slate-400" />
                  Live Updates Disconnected
                </span>
              )}
            </div>
            <p className="text-sm text-slate-500 mt-1">
              Real-time monitoring across approved seat intake, headcount coverage, trust tiers & ML risk ranking.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Academic Year</label>
              <select
                className="text-sm border border-slate-300 rounded-md px-3 py-1.5 bg-white text-slate-800 focus:ring-2 focus:ring-indigo-500"
                value={academicYear}
                onChange={(e) => setAcademicYear(e.target.value)}
              >
                {ACADEMIC_YEARS.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">State Filter</label>
              <select
                className="text-sm border border-slate-300 rounded-md px-3 py-1.5 bg-white text-slate-800 focus:ring-2 focus:ring-indigo-500"
                value={selectedState}
                onChange={(e) => setSelectedState(e.target.value)}
              >
                {STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-end">
              <button
                type="button"
                onClick={loadData}
                disabled={loading}
                className="text-sm px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-md shadow-sm transition disabled:opacity-50"
              >
                {loading ? "Refreshing..." : "Refresh"}
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div className="bg-rose-50 border border-rose-300 text-rose-800 p-4 rounded-md text-sm">
            {error}
          </div>
        )}

        {/* National KPI Cards */}
        {data && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
              <div className="text-xs font-semibold uppercase text-slate-500 tracking-wider">
                Total Institutions
              </div>
              <div className="mt-2 text-3xl font-extrabold text-slate-900">
                {data.total_institutions}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {selectedState === "ALL" ? "Across all 8 participating states" : `In ${selectedState}`}
              </div>
            </div>

            <div className="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
              <div className="text-xs font-semibold uppercase text-slate-500 tracking-wider">
                Students Enrolled
              </div>
              <div className="mt-2 text-3xl font-extrabold text-indigo-600">
                {data.total_students.toLocaleString()}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                Approved & active student records
              </div>
            </div>

            <div className="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
              <div className="text-xs font-semibold uppercase text-slate-500 tracking-wider">
                Enrolment vs Approved Intake
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-3xl font-extrabold text-slate-900">
                  {Math.round(data.enrolment_vs_intake.fill_ratio * 100)}%
                </span>
                <span className="text-xs text-slate-500">
                  ({data.enrolment_vs_intake.actual_enrolment.toLocaleString()} /{" "}
                  {data.enrolment_vs_intake.approved_intake.toLocaleString()})
                </span>
              </div>
              <div className="mt-2 w-full bg-slate-100 rounded-full h-2">
                <div
                  className="bg-indigo-600 h-2 rounded-full"
                  style={{
                    width: `${Math.min(100, data.enrolment_vs_intake.fill_ratio * 100)}%`,
                  }}
                />
              </div>
            </div>

            <div className="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
              <div className="text-xs font-semibold uppercase text-slate-500 tracking-wider">
                Quota Over-Cap Flags
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className={`text-3xl font-extrabold ${data.over_cap.flagged_records > 0 ? "text-rose-600" : "text-emerald-600"}`}>
                  {data.over_cap.flagged_records}
                </span>
                <span className="text-xs text-slate-500">
                  flagged records ({data.over_cap.total_excess_seats} excess seats)
                </span>
              </div>
              <div className="mt-1 text-xs text-slate-500">
                Flagged in atomic Seat Ledger
              </div>
            </div>
          </div>
        )}

        {/* Coverage Meter & Trust Tiers Grid */}
        {data && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Coverage Meter (FR-21) */}
            <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Headcount Coverage Meter (FR-21)</h2>
                  <p className="text-xs text-slate-500">
                    Ratio of verified student records received to declared institutional headcount.
                  </p>
                </div>
                <span
                  className={`px-3 py-1 rounded-full text-xs font-bold ${coverageBandColor(
                    data.coverage.status_band
                  )}`}
                >
                  {data.coverage.status_band} BAND
                </span>
              </div>

              <div className="p-4 rounded-lg border border-slate-200 bg-slate-50">
                <div className="flex justify-between items-baseline mb-2">
                  <span className="text-sm font-medium text-slate-700">National Coverage</span>
                  <span className="text-2xl font-extrabold text-slate-900">
                    {data.coverage.overall_percentage}%
                  </span>
                </div>
                <div className="w-full bg-slate-200 rounded-full h-3 overflow-hidden">
                  <div
                    className={`h-3 rounded-full ${
                      data.coverage.status_band === "GREEN"
                        ? "bg-emerald-500"
                        : data.coverage.status_band === "AMBER"
                        ? "bg-amber-500"
                        : "bg-rose-500"
                    }`}
                    style={{ width: `${Math.min(100, data.coverage.overall_percentage)}%` }}
                  />
                </div>
                <div className="mt-2 flex justify-between text-xs text-slate-500">
                  <span>Received: {data.coverage.total_received.toLocaleString()}</span>
                  <span>Declared: {data.coverage.total_declared.toLocaleString()}</span>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3 pt-2">
                <div className="p-3 rounded-lg border border-emerald-200 bg-emerald-50 text-center">
                  <div className="text-xl font-bold text-emerald-700">
                    {data.coverage.bands.GREEN}
                  </div>
                  <div className="text-xs text-emerald-800 font-medium">GREEN (&ge;90%)</div>
                  <div className="text-[10px] text-emerald-600 mt-0.5">High Compliance</div>
                </div>

                <div className="p-3 rounded-lg border border-amber-200 bg-amber-50 text-center">
                  <div className="text-xl font-bold text-amber-700">
                    {data.coverage.bands.AMBER}
                  </div>
                  <div className="text-xs text-amber-800 font-medium">AMBER (50-89%)</div>
                  <div className="text-[10px] text-amber-600 mt-0.5">Partial Reporting</div>
                </div>

                <div className="p-3 rounded-lg border border-rose-200 bg-rose-50 text-center">
                  <div className="text-xl font-bold text-rose-700">
                    {data.coverage.bands.RED}
                  </div>
                  <div className="text-xs text-rose-800 font-medium">RED (&lt;50%)</div>
                  <div className="text-[10px] text-rose-600 mt-0.5">Headcount Only Risk</div>
                </div>
              </div>
            </div>

            {/* Trust Tier Distribution (FR-20, FR-22) */}
            <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Enrolment by Trust Tier</h2>
                  <p className="text-xs text-slate-500">
                    Progression from raw ingestion (T0) to Student Digilocker Confirmation (T3).
                  </p>
                </div>
                <div className="text-right">
                  <span className="text-xs text-slate-500">T3 Ratio</span>
                  <div className="text-base font-bold text-emerald-600">
                    {Math.round(data.trust_tiers.trust_ratio_t3 * 100)}%
                  </div>
                </div>
              </div>

              <div className="space-y-3 pt-1">
                <div>
                  <div className="flex justify-between text-xs font-semibold text-slate-700 mb-1">
                    <span className="text-emerald-700">T3 — Student Confirmed (OTP/DigiLocker)</span>
                    <span>{data.trust_tiers.T3.toLocaleString()}</span>
                  </div>
                  <div className="w-full bg-slate-100 rounded-full h-2">
                    <div
                      className="bg-emerald-600 h-2 rounded-full"
                      style={{
                        width: `${data.trust_tiers.total ? (data.trust_tiers.T3 / data.trust_tiers.total) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between text-xs font-semibold text-slate-700 mb-1">
                    <span className="text-indigo-700">T2 — APAAR Registry Match Verified</span>
                    <span>{data.trust_tiers.T2.toLocaleString()}</span>
                  </div>
                  <div className="w-full bg-slate-100 rounded-full h-2">
                    <div
                      className="bg-indigo-600 h-2 rounded-full"
                      style={{
                        width: `${data.trust_tiers.total ? (data.trust_tiers.T2 / data.trust_tiers.total) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between text-xs font-semibold text-slate-700 mb-1">
                    <span className="text-blue-700">T1 — Format & Quota Validated</span>
                    <span>{data.trust_tiers.T1.toLocaleString()}</span>
                  </div>
                  <div className="w-full bg-slate-100 rounded-full h-2">
                    <div
                      className="bg-blue-500 h-2 rounded-full"
                      style={{
                        width: `${data.trust_tiers.total ? (data.trust_tiers.T1 / data.trust_tiers.total) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between text-xs font-semibold text-slate-700 mb-1">
                    <span className="text-slate-600">T0 — Raw / Staged Ingestion</span>
                    <span>{data.trust_tiers.T0.toLocaleString()}</span>
                  </div>
                  <div className="w-full bg-slate-100 rounded-full h-2">
                    <div
                      className="bg-slate-400 h-2 rounded-full"
                      style={{
                        width: `${data.trust_tiers.total ? (data.trust_tiers.T0 / data.trust_tiers.total) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>
              </div>

              <div className="pt-2 border-t border-slate-100 text-xs text-slate-500 flex justify-between items-center">
                <span>Total Active Records: {data.trust_tiers.total.toLocaleString()}</span>
                <Link
                  to="/aicte/reports"
                  className="text-indigo-600 hover:text-indigo-800 font-medium"
                >
                  View Tier Report &rarr;
                </Link>
              </div>
            </div>
          </div>
        )}

        {/* State Breakdown & Open Cases Summary */}
        {data && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* State Breakdown Table */}
            <div className="lg:col-span-2 bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Enrolment by State</h2>
                  <p className="text-xs text-slate-500">
                    Institutional breakdown, approved intake vs actual admission counts.
                  </p>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                      <th className="py-2.5 px-3 font-semibold">State</th>
                      <th className="py-2.5 px-3 font-semibold text-center">Institutions</th>
                      <th className="py-2.5 px-3 font-semibold text-right">Approved Intake</th>
                      <th className="py-2.5 px-3 font-semibold text-right">Actual Enrolment</th>
                      <th className="py-2.5 px-3 font-semibold text-right">Fill Ratio</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.state_breakdown.map((row) => (
                      <tr key={row.state} className="hover:bg-slate-50 transition">
                        <td className="py-2 px-3 font-medium text-slate-900">{row.state}</td>
                        <td className="py-2 px-3 text-center text-slate-600">{row.institutions_count}</td>
                        <td className="py-2 px-3 text-right text-slate-600">
                          {row.approved_intake.toLocaleString()}
                        </td>
                        <td className="py-2 px-3 text-right font-medium text-indigo-600">
                          {row.actual_enrolment.toLocaleString()}
                        </td>
                        <td className="py-2 px-3 text-right">
                          <span
                            className={`font-semibold ${
                              row.fill_ratio > 1.0
                                ? "text-rose-600"
                                : row.fill_ratio >= 0.8
                                ? "text-emerald-600"
                                : "text-amber-600"
                            }`}
                          >
                            {Math.round(row.fill_ratio * 100)}%
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Review Queue Summary */}
            <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Open Cases (FR-17b)</h2>
                  <p className="text-xs text-slate-500">Pending AICTE review action.</p>
                </div>
                <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-amber-100 text-amber-800">
                  {data.cases.total_open} Open
                </span>
              </div>

              <div className="space-y-2.5">
                <div className="flex justify-between items-center p-2.5 rounded-md bg-slate-50 border border-slate-200">
                  <span className="text-xs font-medium text-slate-700">Cross-Institution Duplicates</span>
                  <span className="text-xs font-bold px-2 py-0.5 rounded bg-rose-100 text-rose-800">
                    {data.cases.cross_institution_duplicates}
                  </span>
                </div>

                <div className="flex justify-between items-center p-2.5 rounded-md bg-slate-50 border border-slate-200">
                  <span className="text-xs font-medium text-slate-700">Quota Over-Cap Cases</span>
                  <span className="text-xs font-bold px-2 py-0.5 rounded bg-amber-100 text-amber-800">
                    {data.cases.by_type["SEAT_OVER_CAP"] || 0}
                  </span>
                </div>

                <div className="flex justify-between items-center p-2.5 rounded-md bg-slate-50 border border-slate-200">
                  <span className="text-xs font-medium text-slate-700">Anomaly Detections</span>
                  <span className="text-xs font-bold px-2 py-0.5 rounded bg-purple-100 text-purple-800">
                    {data.cases.by_type["ANOMALY"] || 0}
                  </span>
                </div>

                <div className="flex justify-between items-center p-2.5 rounded-md bg-slate-50 border border-slate-200">
                  <span className="text-xs font-medium text-slate-700">Shared Identity Rings</span>
                  <span className="text-xs font-bold px-2 py-0.5 rounded bg-indigo-100 text-indigo-800">
                    {data.cases.by_type["RING"] || 0}
                  </span>
                </div>

                <div className="flex justify-between items-center p-2.5 rounded-md bg-slate-50 border border-slate-200">
                  <span className="text-xs font-medium text-slate-700">Timeline Inconsistencies</span>
                  <span className="text-xs font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-800">
                    {data.cases.by_type["TIMELINE"] || 0}
                  </span>
                </div>
              </div>

              <div className="pt-2">
                <Link
                  to="/aicte/review"
                  className="block w-full py-2 px-3 text-center text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-md transition shadow-sm"
                >
                  Open AICTE Review Queue &rarr;
                </Link>
              </div>
            </div>
          </div>
        )}

        {/* Institution Risk Leaderboard (FR-25) */}
        {data && (
          <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
              <div>
                <h2 className="text-lg font-bold text-slate-900">
                  Institution Risk Leaderboard (FR-25)
                </h2>
                <p className="text-xs text-slate-500">
                  Transparent 4-factor risk score: 35% Anomaly + 25% Duplicate Rate + 20% Identity Rings + 20% Rule Violations.
                </p>
              </div>
              <Link
                to="/aicte/reports"
                className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
              >
                Download Full Ranking CSV &rarr;
              </Link>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                    <th className="py-2.5 px-3 font-semibold text-center">Rank</th>
                    <th className="py-2.5 px-3 font-semibold">Institution</th>
                    <th className="py-2.5 px-3 font-semibold">State</th>
                    <th className="py-2.5 px-3 font-semibold text-right">Composite Risk</th>
                    <th className="py-2.5 px-3 font-semibold text-center">Component Breakdown</th>
                    <th className="py-2.5 px-3 font-semibold">Top Regulatory Alerts</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.risk_leaderboard.map((inst) => {
                    const isHighRisk = inst.risk_score >= 50;
                    return (
                      <tr key={inst.institution_id} className="hover:bg-slate-50 transition">
                        <td className="py-3 px-3 text-center font-bold text-slate-700">
                          #{inst.rank}
                        </td>
                        <td className="py-3 px-3">
                          <div className="font-semibold text-slate-900">{inst.institution_name}</div>
                          <div className="text-[11px] font-mono text-slate-500">
                            {inst.institution_code}
                          </div>
                        </td>
                        <td className="py-3 px-3 text-slate-600">{inst.state}</td>
                        <td className="py-3 px-3 text-right">
                          <span
                            className={`px-2 py-0.5 rounded text-xs font-bold ${
                              isHighRisk
                                ? "bg-rose-100 text-rose-800"
                                : inst.risk_score >= 25
                                ? "bg-amber-100 text-amber-800"
                                : "bg-emerald-100 text-emerald-800"
                            }`}
                          >
                            {inst.risk_score} / 100
                          </span>
                        </td>
                        <td className="py-3 px-3">
                          <div className="flex items-center gap-1.5 justify-center text-[10px]">
                            <span
                              className="px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200"
                              title="Anomaly Component (35%)"
                            >
                              Anom: {inst.components.anomaly.weighted}
                            </span>
                            <span
                              className="px-1.5 py-0.5 rounded bg-rose-50 text-rose-700 border border-rose-200"
                              title="Duplicate Rate Component (25%)"
                            >
                              Dup: {inst.components.duplicate_rate.weighted}
                            </span>
                            <span
                              className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200"
                              title="Ring Flags Component (20%)"
                            >
                              Ring: {inst.components.ring_flags.weighted}
                            </span>
                            <span
                              className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                              title="Rule Violations Component (20%)"
                            >
                              Viol: {inst.components.rule_violations.weighted}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-3 text-slate-700">
                          {inst.alerts.length > 0 ? (
                            <div className="space-y-0.5">
                              {inst.alerts.slice(0, 2).map((a, i) => (
                                <div key={i} className="text-[11px] text-slate-600 flex items-start gap-1">
                                  <span className="text-amber-500 font-bold">&bull;</span>
                                  <span>{a}</span>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <span className="text-xs text-emerald-600 font-medium">
                              Clean record
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Reports Navigation Bar */}
        <div className="flex items-center justify-between p-4 bg-indigo-50 border border-indigo-100 rounded-lg">
          <div>
            <div className="text-sm font-semibold text-indigo-900">
              Need statutory CSV exports for AICTE Council, State Directorates or Audit?
            </div>
            <div className="text-xs text-indigo-700 mt-0.5">
              Download standard reports for Enrolment vs Intake, Supernumerary Utilisation, Over-Cap Flags, Cross-Duplicates, Coverage & Risk.
            </div>
          </div>
          <Link
            to="/aicte/reports"
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-md shadow-sm transition"
          >
            Access Regulatory Reports &rarr;
          </Link>
        </div>

        <div className="text-xs text-slate-400 text-right">
          Last updated: {lastUpdated.toLocaleTimeString()}
        </div>
      </div>
    </AppShell>
  );
}
