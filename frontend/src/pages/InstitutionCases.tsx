import { useEffect, useState } from "react";
import { AppShell } from "../components/AppShell";
import { listCases, type CaseItem } from "../api/cases";
import { useAuth } from "../hooks/useAuth";

export function InstitutionCasesPage() {
  const { user } = useAuth();
  const [cases, setCases] = useState<CaseItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const data = await listCases();
        setCases(data);
      } catch (err) {
        console.error("Failed to load institution cases", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  return (
    <AppShell area="institution">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Institution Cases & Notices</h1>
        <p className="text-sm text-slate-500">
          Cases and validation notices requiring attention for {user?.institution?.name || "your institution"}.
        </p>
      </div>

      <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
        <div className="p-4 border-b bg-slate-50 text-xs font-semibold text-slate-600 flex justify-between">
          <span>CASES ({cases.length})</span>
          {loading && <span className="text-slate-400">Loading…</span>}
        </div>
        <div className="divide-y divide-slate-100">
          {cases.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-400">
              No open cases or compliance notices for your institution.
            </div>
          ) : (
            cases.map((c) => (
              <div key={c.id} className="p-4 text-sm hover:bg-slate-50">
                <div className="flex justify-between items-start mb-1">
                  <span className="font-semibold text-slate-800">
                    Case #{c.id} • {c.type}
                  </span>
                  <span
                    className={`text-xs px-2 py-0.5 rounded font-mono ${
                      c.status === "OPEN"
                        ? "bg-amber-100 text-amber-800"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {c.status}
                  </span>
                </div>
                <p className="text-slate-600 mt-1">
                  {String(c.explanation_json?.summary_en || "Case requires review or correction.")}
                </p>
                {c.decision_note && (
                  <div className="mt-2 text-xs bg-slate-50 p-2 rounded border border-slate-200 text-slate-700">
                    <span className="font-semibold">Reviewer Note:</span> {c.decision_note}
                  </div>
                )}
                <div className="flex gap-4 text-xs text-slate-400 mt-2">
                  <span>Priority: {c.priority}</span>
                  <span>Date: {new Date(c.created_at).toLocaleDateString()}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </AppShell>
  );
}
