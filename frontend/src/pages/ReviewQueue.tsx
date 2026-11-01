import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../components/AppShell";
import {
  listCases,
  submitCaseDecision,
  triggerRetrain,
  triggerTriage,
  type CaseItem,
} from "../api/cases";
import { useAuth } from "../hooks/useAuth";

const CASE_TYPES = [
  "ALL",
  "DUP_FUZZY",
  "SEAT_OVER_CAP",
  "ANOMALY",
  "RING",
  "TIMELINE",
  "DUP_EXACT_CROSS",
];

const STATUS_FILTERS = ["OPEN", "CONFIRMED", "DISMISSED", "ESCALATED", "RESOLVED", "ALL"];

interface DuplicateRecordDetail {
  id: number;
  institution_id: number;
  name_norm: string;
  dob?: string | null;
  programme_code?: string | null;
}

interface DuplicateFeatures {
  jaro_winkler?: number;
  token_sort_ratio?: number;
  phonetic_match?: number;
  dob_day_month_swap?: number;
  phone_match?: number;
  interval_overlap?: number;
}

interface AnomalyTopFeature {
  feature: string;
  z_score: number;
  value: number;
  peer_mean: number;
}

export function ReviewQueuePage() {
  const { user } = useAuth();
  const [cases, setCases] = useState<CaseItem[]>([]);
  const [selectedCase, setSelectedCase] = useState<CaseItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("OPEN");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const loadCases = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listCases({
        type: typeFilter === "ALL" ? undefined : typeFilter,
        status: statusFilter === "ALL" ? undefined : statusFilter,
      });
      setCases(data);
      if (selectedCase) {
        const refreshed = data.find((c) => c.id === selectedCase.id);
        setSelectedCase(refreshed || null);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Error loading cases: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter, selectedCase]);

  useEffect(() => {
    loadCases();
  }, [typeFilter, statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDecision = async (action: string) => {
    if (!selectedCase) return;
    if (note.trim().length < 3) {
      setActionMessage("Please provide a note with at least 3 characters.");
      return;
    }
    setSubmitting(true);
    setActionMessage(null);
    try {
      await submitCaseDecision(selectedCase.id, { action, note: note.trim() });
      setActionMessage(`Decision '${action}' saved successfully.`);
      setNote("");
      await loadCases();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Failed to submit decision: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetrain = async () => {
    setSubmitting(true);
    try {
      const res = await triggerRetrain();
      const report = res.report as Record<string, unknown> | undefined;
      const inDist = report?.in_distribution as Record<string, unknown> | undefined;
      setActionMessage(`Model retrained successfully with feedback: F1=${String(inDist?.f1 ?? "N/A")}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Retrain failed: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleTriage = async () => {
    setSubmitting(true);
    try {
      const res = await triggerTriage();
      setActionMessage(
        `Triage finished: ${String(res.duplicate_cases_opened ?? 0)} duplicates, ${String(
          res.ring_cases_opened ?? 0
        )} rings, ${String(res.anomaly_cases_opened ?? 0)} anomalies opened.`
      );
      await loadCases();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Triage failed: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const renderEvidence = (c: CaseItem) => {
    const ev = c.evidence_json || {};
    if (c.type === "DUP_FUZZY" && ev.record_a && ev.record_b) {
      const recA = ev.record_a as DuplicateRecordDetail;
      const recB = ev.record_b as DuplicateRecordDetail;
      const feats = (ev.features as DuplicateFeatures) || {};
      const prob = Number(c.score ?? ev.match_probability ?? 0);
      return (
        <div className="space-y-4">
          <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            <span className="font-semibold">Match Probability:</span>{" "}
            {(prob * 100).toFixed(1)}% ({String(ev.label || "fuzzy duplicate candidate")})
          </div>
          <table className="w-full text-left text-sm border-collapse">
            <thead>
              <tr className="border-b bg-slate-50 text-slate-600">
                <th className="p-2">Field</th>
                <th className="p-2">Record #{recA.id} (Inst {recA.institution_id})</th>
                <th className="p-2">Record #{recB.id} (Inst {recB.institution_id})</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b">
                <td className="p-2 font-medium">Name</td>
                <td className="p-2">{recA.name_norm}</td>
                <td className="p-2">{recB.name_norm}</td>
              </tr>
              <tr className="border-b">
                <td className="p-2 font-medium">DOB</td>
                <td className="p-2">{recA.dob || "—"}</td>
                <td className="p-2">{recB.dob || "—"}</td>
              </tr>
              <tr className="border-b">
                <td className="p-2 font-medium">Programme</td>
                <td className="p-2">{recA.programme_code || "—"}</td>
                <td className="p-2">{recB.programme_code || "—"}</td>
              </tr>
            </tbody>
          </table>
          <div className="grid grid-cols-2 gap-2 text-xs bg-slate-50 p-3 rounded">
            <div>Jaro-Winkler: {((feats.jaro_winkler ?? 0) * 100).toFixed(1)}%</div>
            <div>Token Sort Ratio: {((feats.token_sort_ratio ?? 0) * 100).toFixed(1)}%</div>
            <div>Phonetic Match: {feats.phonetic_match ? "Yes" : "No"}</div>
            <div>DOB Swap Detected: {feats.dob_day_month_swap ? "Yes" : "No"}</div>
            <div>Phone Match: {feats.phone_match ? "Yes" : "No"}</div>
            <div>Interval Overlap: {feats.interval_overlap ? "Yes" : "No"}</div>
          </div>
        </div>
      );
    }

    if (c.type === "ANOMALY") {
      const topFeats = (ev.top_features as AnomalyTopFeature[]) || [];
      return (
        <div className="space-y-3">
          <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            <span className="font-semibold">Anomaly Score:</span>{" "}
            {((c.score || 0) * 100).toFixed(1)}%
          </div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Top Statistical Deviations (Peer z-scores)
          </h4>
          <div className="space-y-2">
            {topFeats.map((tf, idx) => (
              <div key={idx} className="flex justify-between items-center bg-slate-50 p-2 rounded text-sm">
                <span className="font-mono text-xs">{tf.feature}</span>
                <span className="text-slate-600">
                  Val: {tf.value} (Peer μ: {tf.peer_mean})
                </span>
                <span
                  className={`font-semibold px-2 py-0.5 rounded text-xs ${
                    Math.abs(tf.z_score) >= 2.5
                      ? "bg-rose-100 text-rose-700"
                      : "bg-amber-100 text-amber-700"
                  }`}
                >
                  z = {tf.z_score > 0 ? `+${tf.z_score}` : tf.z_score}
                </span>
              </div>
            ))}
          </div>
        </div>
      );
    }

    if (c.type === "RING") {
      const size = Number(ev.total_records_count ?? ev.size ?? 0);
      const insts = (ev.institutions_involved as string[]) || [];
      const instCount = Number(ev.institutions_count ?? insts.length);
      return (
        <div className="space-y-3">
          <div className="rounded border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-800">
            <span className="font-semibold">Cluster Size:</span> {size} records across {instCount} institutions
          </div>
          <div className="text-sm">
            <span className="text-slate-500">Shared Identifier:</span>{" "}
            <span className="font-mono font-medium">{String(ev.masked_token || "—")}</span>
          </div>
          <div>
            <span className="text-xs font-semibold text-slate-500">Institutions Involved:</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {insts.map((code) => (
                <span key={code} className="bg-slate-200 text-slate-800 px-2 py-0.5 rounded text-xs font-mono">
                  {code}
                </span>
              ))}
            </div>
          </div>
        </div>
      );
    }

    if (c.type === "SEAT_OVER_CAP") {
      return (
        <div className="space-y-3">
          <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            Bucket excess: <span className="font-bold">{String(ev.excess ?? 0)} seats</span> over cap
          </div>
          <div className="text-sm">
            Bucket: <span className="font-mono font-medium">{String(ev.bucket ?? "—")}</span> | Cap: {String(ev.cap ?? 0)} | Used: {String(ev.used ?? 0)}
          </div>
        </div>
      );
    }

    return (
      <pre className="text-xs bg-slate-100 p-3 rounded overflow-x-auto max-h-60">
        {JSON.stringify(ev, null, 2)}
      </pre>
    );
  };

  return (
    <AppShell area="aicte">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">AICTE Review Queue</h1>
          <p className="text-sm text-slate-500">
            Human-in-the-loop review for duplicates, anomalies, identity rings, and quota flags.
          </p>
        </div>
        {user?.role === "AICTE_ADMIN" && (
          <div className="flex gap-2">
            <button
              onClick={handleTriage}
              disabled={submitting}
              className="px-3 py-1.5 text-xs font-medium rounded border border-slate-300 bg-white hover:bg-slate-50 text-slate-700 shadow-sm"
            >
              Run Triage
            </button>
            <button
              onClick={handleRetrain}
              disabled={submitting}
              className="px-3 py-1.5 text-xs font-medium rounded bg-brand-600 hover:bg-brand-700 text-white shadow-sm"
            >
              Retrain with Feedback
            </button>
          </div>
        )}
      </div>

      {actionMessage && (
        <div className="mb-4 p-3 text-sm rounded bg-blue-50 border border-blue-200 text-blue-800 flex justify-between">
          <span>{actionMessage}</span>
          <button onClick={() => setActionMessage(null)} className="font-bold ml-2">×</button>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-4 bg-white p-4 rounded-lg border border-slate-200 shadow-sm text-sm">
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">Case Type</label>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            {CASE_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">Status</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex items-end">
          <button
            onClick={loadCases}
            className="px-3 py-1 text-sm rounded border border-slate-300 hover:bg-slate-50"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Case List */}
        <div className="lg:col-span-5 bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden flex flex-col h-[650px]">
          <div className="p-3 border-b bg-slate-50 text-xs font-semibold text-slate-600 flex justify-between">
            <span>CASES ({cases.length})</span>
            {loading && <span className="text-slate-400">Loading…</span>}
          </div>
          <div className="overflow-y-auto divide-y divide-slate-100 flex-1">
            {cases.length === 0 ? (
              <div className="p-8 text-center text-sm text-slate-400">No cases match the filter.</div>
            ) : (
              cases.map((c) => (
                <div
                  key={c.id}
                  onClick={() => setSelectedCase(c)}
                  className={`p-3 text-sm cursor-pointer transition-colors ${
                    selectedCase?.id === c.id
                      ? "bg-brand-50 border-l-4 border-brand-600"
                      : "hover:bg-slate-50"
                  }`}
                >
                  <div className="flex justify-between items-start mb-1">
                    <span className="font-semibold text-slate-800">#{c.id} {c.type}</span>
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                        c.status === "OPEN"
                          ? "bg-amber-100 text-amber-800"
                          : c.status === "CONFIRMED"
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {c.status}
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 line-clamp-2">
                    {String(c.explanation_json?.summary_en || "Case pending review")}
                  </div>
                  <div className="flex gap-3 text-[11px] text-slate-400 mt-2">
                    <span>Priority: {c.priority}</span>
                    {c.score != null && <span>Score: {(c.score * 100).toFixed(0)}%</span>}
                    <span>{new Date(c.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Case Detail & Decisions */}
        <div className="lg:col-span-7 bg-white rounded-lg border border-slate-200 shadow-sm p-6 flex flex-col justify-between">
          {selectedCase ? (
            <div className="space-y-6">
              <div className="border-b pb-4">
                <div className="flex justify-between items-start">
                  <div>
                    <h2 className="text-xl font-bold text-slate-900">
                      Case #{selectedCase.id}: {selectedCase.type}
                    </h2>
                    <p className="text-xs text-slate-500 mt-1">
                      Created on {new Date(selectedCase.created_at).toLocaleString()} • Priority {selectedCase.priority}
                    </p>
                  </div>
                  <span className="text-xs font-mono bg-slate-100 text-slate-700 px-2 py-1 rounded">
                    {selectedCase.visibility}
                  </span>
                </div>
                <p className="mt-3 text-sm font-medium text-slate-700 bg-slate-50 p-3 rounded border border-slate-100">
                  {String(selectedCase.explanation_json?.summary_en || "")}
                </p>
              </div>

              {/* Evidence Inspector */}
              <div>
                <h3 className="text-sm font-semibold text-slate-800 mb-2">Evidence & Features</h3>
                {renderEvidence(selectedCase)}
              </div>

              {/* Existing Decision if resolved */}
              {selectedCase.decided_at && (
                <div className="rounded bg-slate-50 p-3 border border-slate-200 text-xs text-slate-600 space-y-1">
                  <div className="font-semibold text-slate-800">Review Outcome:</div>
                  <div>Status: <span className="font-mono">{selectedCase.status}</span></div>
                  <div>Note: {selectedCase.decision_note}</div>
                  <div>Decided At: {new Date(selectedCase.decided_at).toLocaleString()}</div>
                </div>
              )}

              {/* Decision Actions */}
              {selectedCase.status === "OPEN" && (
                <div className="border-t pt-4 space-y-3">
                  <h3 className="text-sm font-semibold text-slate-800">Make Review Decision</h3>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">
                      Reviewer Note (required, min 3 characters)
                    </label>
                    <textarea
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="Enter justification or audit decision notes..."
                      className="w-full border border-slate-300 rounded p-2 text-sm focus:ring-1 focus:ring-brand-500"
                      rows={2}
                    />
                  </div>

                  <div className="flex flex-wrap gap-2 pt-1">
                    {selectedCase.type === "DUP_FUZZY" && (
                      <>
                        <button
                          onClick={() => handleDecision("CONFIRM_DUPLICATE")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-emerald-600 hover:bg-emerald-700 text-white"
                        >
                          Confirm Duplicate
                        </button>
                        <button
                          onClick={() => handleDecision("NOT_DUPLICATE")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-slate-600 hover:bg-slate-700 text-white"
                        >
                          Not a Duplicate
                        </button>
                        <button
                          onClick={() => handleDecision("REQUEST_CORRECTION")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-amber-600 hover:bg-amber-700 text-white"
                        >
                          Request Correction
                        </button>
                        <button
                          onClick={() => handleDecision("ESCALATE")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded border border-slate-300 hover:bg-slate-50 text-slate-700"
                        >
                          Escalate
                        </button>
                      </>
                    )}

                    {selectedCase.type === "ANOMALY" && (
                      <>
                        <button
                          onClick={() => handleDecision("ACKNOWLEDGE")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-indigo-600 hover:bg-indigo-700 text-white"
                        >
                          Acknowledge
                        </button>
                        <button
                          onClick={() => handleDecision("ESCALATE")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-rose-600 hover:bg-rose-700 text-white"
                        >
                          Escalate for Audit
                        </button>
                        <button
                          onClick={() => handleDecision("DISMISS")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded border border-slate-300 hover:bg-slate-50 text-slate-700"
                        >
                          Dismiss
                        </button>
                      </>
                    )}

                    {selectedCase.type === "RING" && (
                      <>
                        <button
                          onClick={() => handleDecision("CONFIRM")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-rose-600 hover:bg-rose-700 text-white"
                        >
                          Confirm Ring
                        </button>
                        <button
                          onClick={() => handleDecision("DISMISS")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded border border-slate-300 hover:bg-slate-50 text-slate-700"
                        >
                          Dismiss Ring
                        </button>
                        <button
                          onClick={() => handleDecision("ESCALATE")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded border border-slate-300 hover:bg-slate-50 text-slate-700"
                        >
                          Escalate
                        </button>
                      </>
                    )}

                    {selectedCase.type === "SEAT_OVER_CAP" && (
                      <>
                        <button
                          onClick={() => handleDecision("ACCEPT_EXCEPTION")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-emerald-600 hover:bg-emerald-700 text-white"
                        >
                          Accept Exception
                        </button>
                        <button
                          onClick={() => handleDecision("REQUEST_CORRECTION")}
                          disabled={submitting}
                          className="px-3 py-1.5 text-xs font-medium rounded bg-amber-600 hover:bg-amber-700 text-white"
                        >
                          Request Correction
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-slate-400 text-sm">
              Select a case from the list on the left to inspect evidence and record a review decision.
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
