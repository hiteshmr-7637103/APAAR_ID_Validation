import { useState, useEffect } from "react";
import { listQuotaRules, updateQuotaRule, type QuotaRuleItem } from "../api/rules";

export function RulesEditorPage() {
  const [rules, setRules] = useState<QuotaRuleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingRule, setEditingRule] = useState<QuotaRuleItem | null>(null);
  const [editJson, setEditJson] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function loadRules() {
    try {
      setLoading(true);
      const res = await listQuotaRules();
      setRules(res);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load rules");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRules();
  }, []);

  function handleEditClick(rule: QuotaRuleItem) {
    setEditingRule(rule);
    setEditJson(JSON.stringify(rule.active_value, null, 2));
    setEditDesc(rule.description || "");
    setError(null);
    setSuccess(null);
  }

  async function handleSave() {
    if (!editingRule) return;
    try {
      setSaving(true);
      setError(null);
      let parsedValue: Record<string, unknown>;
      try {
        parsedValue = JSON.parse(editJson);
      } catch {
        setError("Invalid JSON format. Please verify syntax.");
        setSaving(false);
        return;
      }

      await updateQuotaRule(editingRule.key, {
        value_json: parsedValue,
        description: editDesc,
      });

      setSuccess(`Successfully updated rule '${editingRule.key}'! New version created.`);
      setEditingRule(null);
      await loadRules();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update rule.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="p-8 text-slate-500">Loading statutory quota rules...</div>;
  }

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-200 pb-6">
        <div>
          <h1 className="text-3xl font-bold text-slate-900 tracking-tight">Statutory Rules-as-Data Editor</h1>
          <p className="text-slate-500 text-sm mt-1">
            Versioned AICTE quota policies, supernumerary caps, and admission windows.
          </p>
        </div>
      </div>

      {success && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-800 text-sm">
          {success}
        </div>
      )}

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-lg text-rose-800 text-sm">
          {error}
        </div>
      )}

      {/* Rules Table */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-600 font-semibold text-xs uppercase tracking-wider">
              <tr>
                <th className="py-3 px-4">Rule Key</th>
                <th className="py-3 px-4">Description</th>
                <th className="py-3 px-4">Active Value</th>
                <th className="py-3 px-4">Version</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rules.map((r) => (
                <tr key={r.key} className="hover:bg-slate-50/60 transition-colors">
                  <td className="py-3 px-4 font-mono font-semibold text-slate-800 text-xs">
                    {r.key}
                  </td>
                  <td className="py-3 px-4 text-slate-600 text-xs max-w-md">
                    {r.description}
                  </td>
                  <td className="py-3 px-4 font-mono text-xs text-slate-700">
                    <pre className="bg-slate-50 p-1.5 rounded border border-slate-100 max-h-24 overflow-y-auto">
                      {JSON.stringify(r.active_value, null, 2)}
                    </pre>
                  </td>
                  <td className="py-3 px-4 text-xs font-mono">
                    {r.is_default ? (
                      <span className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded">DEFAULT</span>
                    ) : (
                      <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded font-semibold">v{r.active_version}</span>
                    )}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <button
                      onClick={() => handleEditClick(r)}
                      className="px-3 py-1.5 text-xs bg-slate-900 hover:bg-slate-800 text-white font-medium rounded-lg shadow-sm"
                    >
                      Edit Rule
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit Rule Modal */}
      {editingRule && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-xl w-full p-6 space-y-4">
            <div className="flex justify-between items-center border-b border-slate-100 pb-3">
              <h3 className="font-bold text-slate-900 text-base">Edit Rule: {editingRule.key}</h3>
              <button
                onClick={() => setEditingRule(null)}
                className="text-slate-400 hover:text-slate-600 text-lg leading-none"
              >
                &times;
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Rule Description / Justification
                </label>
                <input
                  type="text"
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  className="w-full text-xs p-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-900"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Value JSON
                </label>
                <textarea
                  rows={8}
                  value={editJson}
                  onChange={(e) => setEditJson(e.target.value)}
                  className="w-full font-mono text-xs p-3 border border-slate-200 rounded-lg bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-900"
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-3 border-t border-slate-100">
              <button
                onClick={() => setEditingRule(null)}
                className="px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-100 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 text-xs font-semibold bg-slate-900 hover:bg-slate-800 text-white rounded-lg disabled:opacity-50"
              >
                {saving ? "Saving Version..." : "Append New Version"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
export default RulesEditorPage;
