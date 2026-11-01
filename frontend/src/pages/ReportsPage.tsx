import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../components/AppShell";
import {
  fetchReportsList,
  fetchReportPreview,
  downloadReportCsv,
  type ReportMeta,
  type ReportPreview,
} from "../api/reports";

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

export function ReportsPage() {
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [selectedReport, setSelectedReport] = useState("enrolment-vs-intake");
  const [academicYear, setAcademicYear] = useState("2024-2025");
  const [selectedState, setSelectedState] = useState("ALL");
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchReportsList()
      .then((res) => {
        setReports(res.reports);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Failed to load reports catalog: ${msg}`);
      });
  }, []);

  const loadPreview = useCallback(async () => {
    if (!selectedReport) return;
    setLoading(true);
    setError(null);
    try {
      const stateParam = selectedState === "ALL" ? undefined : selectedState;
      const data = await fetchReportPreview(selectedReport, academicYear, stateParam);
      setPreview(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Failed to generate preview: ${msg}`);
      setPreview(null);
    } finally {
      setLoading(false);
    }
  }, [selectedReport, academicYear, selectedState]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const stateParam = selectedState === "ALL" ? undefined : selectedState;
      await downloadReportCsv(selectedReport, academicYear, stateParam);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Download failed: ${msg}`);
    } finally {
      setDownloading(false);
    }
  };

  const currentMeta = reports.find((r) => r.name === selectedReport);

  return (
    <AppShell area="aicte">
      <div className="space-y-6">
        {/* Header */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                AICTE Regulatory & Statutory Reports (FR-24)
              </h1>
              <p className="text-sm text-slate-500 mt-1">
                Standardized regulatory reports with academic-year and state filters for compliance audit and council reporting.
              </p>
            </div>
            <div>
              <button
                type="button"
                onClick={handleDownload}
                disabled={downloading || loading || !preview}
                className="inline-flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-md shadow-sm transition disabled:opacity-50"
              >
                <span>&darr;</span>
                {downloading ? "Generating CSV..." : "Download CSV"}
              </button>
            </div>
          </div>

          {/* Filter Bar */}
          <div className="mt-6 pt-4 border-t border-slate-100 grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">
                Select Report
              </label>
              <select
                className="w-full text-sm border border-slate-300 rounded-md px-3 py-2 bg-white text-slate-800 focus:ring-2 focus:ring-indigo-500"
                value={selectedReport}
                onChange={(e) => setSelectedReport(e.target.value)}
              >
                {reports.map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.title}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">
                Academic Year
              </label>
              <select
                className="w-full text-sm border border-slate-300 rounded-md px-3 py-2 bg-white text-slate-800 focus:ring-2 focus:ring-indigo-500"
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
              <label className="block text-xs font-medium text-slate-700 mb-1">
                State Filter
              </label>
              <select
                className="w-full text-sm border border-slate-300 rounded-md px-3 py-2 bg-white text-slate-800 focus:ring-2 focus:ring-indigo-500"
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
          </div>

          {currentMeta && (
            <div className="mt-4 p-3 rounded-md bg-slate-50 border border-slate-200 text-xs text-slate-600">
              <span className="font-semibold text-slate-900">{currentMeta.title}:</span>{" "}
              {currentMeta.description}
            </div>
          )}
        </div>

        {error && (
          <div className="bg-rose-50 border border-rose-300 text-rose-800 p-4 rounded-md text-sm">
            {error}
          </div>
        )}

        {/* Data Preview Table */}
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50">
            <div className="text-sm font-bold text-slate-800">
              Report Preview {preview && `(${preview.total_rows} records)`}
            </div>
            {loading && <div className="text-xs text-indigo-600 font-medium">Loading report...</div>}
          </div>

          {preview && preview.rows.length > 0 ? (
            <div className="overflow-x-auto max-h-[600px]">
              <table className="w-full text-left text-xs border-collapse">
                <thead className="sticky top-0 bg-slate-100 text-slate-700 border-b border-slate-300 z-10">
                  <tr>
                    {preview.headers.map((h, i) => (
                      <th key={i} className="py-2.5 px-3 font-semibold whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {preview.rows.map((row, rIdx) => (
                    <tr key={rIdx} className="hover:bg-slate-50 transition">
                      {row.map((cell, cIdx) => (
                        <td key={cIdx} className="py-2.5 px-3 text-slate-700 whitespace-nowrap">
                          {cell === null || cell === undefined || cell === "" ? (
                            <span className="text-slate-400">-</span>
                          ) : typeof cell === "number" ? (
                            cell.toLocaleString()
                          ) : (
                            String(cell)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : !loading ? (
            <div className="p-8 text-center text-sm text-slate-500">
              No data returned for the selected academic year and state.
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-slate-500">
              Generating report table...
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
