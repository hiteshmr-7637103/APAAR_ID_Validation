import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { listAudit, verifyAudit } from "../api/audit";
import { t } from "../i18n";

/** Audit table with "Verify integrity" (spec 11 item 13; the rules editor UI comes later). */
export function AuditLogPage() {
  const [action, setAction] = useState("");
  const entries = useQuery({
    queryKey: ["audit", action],
    queryFn: () => listAudit(action || undefined),
  });
  const verify = useMutation({ mutationFn: verifyAudit });
  const v = verify.data;

  return (
    <AppShell area="aicte">
      <h1 className="text-2xl font-semibold">{t("audit.title")}</h1>
      <p className="mt-1 max-w-3xl text-sm text-slate-500">{t("audit.subtitle")}</p>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => verify.mutate()}
          disabled={verify.isPending}
          className="rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {verify.isPending ? t("audit.verifying") : t("audit.verify")}
        </button>
        <label className="text-sm">
          Action{" "}
          <input
            value={action}
            onChange={(e) => setAction(e.target.value.toUpperCase())}
            placeholder="e.g. BATCH_REJECT"
            className="ml-1 rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
      </div>

      {v && (
        <div
          role="status"
          className={`mt-4 rounded-lg border p-3 text-sm ${
            v.valid
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border-red-200 bg-red-50 text-red-800"
          }`}
        >
          {v.valid ? (
            <>
              {t("audit.valid")} {v.entries ?? v.entries_checked} entries checked; head hash{" "}
              <code className="break-all text-xs">{v.head_hash}</code>
            </>
          ) : (
            <>
              {t("audit.broken")} #{v.first_broken?.index} (id {v.first_broken?.id},{" "}
              {v.first_broken?.action}): {v.first_broken?.reason}
            </>
          )}
        </div>
      )}

      <div className="mt-5 overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-2 py-1">ID</th>
              <th className="px-2 py-1">Time</th>
              <th className="px-2 py-1">Actor</th>
              <th className="px-2 py-1">Action</th>
              <th className="px-2 py-1">Entity</th>
              <th className="px-2 py-1">Hash</th>
            </tr>
          </thead>
          <tbody>
            {entries.data?.entries.map((e) => (
              <tr
                key={e.id}
                className={`border-t border-slate-100 ${v?.first_broken?.id === e.id ? "bg-red-50" : ""}`}
              >
                <td className="px-2 py-1 font-mono text-xs">{e.id}</td>
                <td className="px-2 py-1 text-xs">{new Date(e.ts).toLocaleString()}</td>
                <td className="px-2 py-1 text-xs">
                  {e.actor_role ?? "system"} {e.actor_id != null && `#${e.actor_id}`}
                </td>
                <td className="px-2 py-1 font-mono text-xs">{e.action}</td>
                <td className="px-2 py-1 text-xs">
                  {e.entity_type} {e.entity_id}
                </td>
                <td className="px-2 py-1 font-mono text-xs" title={e.hash}>
                  {e.hash.slice(0, 12)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries.data && (
          <p className="p-2 text-xs text-slate-500">
            Showing {entries.data.entries.length} of {entries.data.total} entries (newest first).
          </p>
        )}
      </div>
    </AppShell>
  );
}
