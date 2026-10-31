import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { ApiError } from "../api/client";
import { BucketPanel } from "../components/BucketPanel";
import { decideBatch, getBatch, getBatchRecords, listBatches, type Batch } from "../api/uploads";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";

const STATUS_STYLE: Record<string, string> = {
  PENDING_APPROVAL: "bg-amber-50 text-amber-900",
  APPROVED: "bg-emerald-50 text-emerald-800",
  REJECTED: "bg-slate-100 text-slate-700",
  DRY_RUN: "bg-sky-50 text-sky-900",
  DRAFT: "bg-slate-50 text-slate-600",
};

function BatchList({
  batches,
  selected,
  onSelect,
}: {
  batches: Batch[];
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white">
      {batches.map((b) => (
        <li key={b.id}>
          <button
            type="button"
            onClick={() => onSelect(b.id)}
            aria-current={selected === b.id}
            className={`w-full px-3 py-2 text-left text-sm hover:bg-slate-50 ${
              selected === b.id ? "bg-brand-50" : ""
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="font-medium">
                #{b.id} {b.filename ?? b.source}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[b.status] ?? ""}`}>
                {b.status}
              </span>
            </div>
            <div className="text-xs text-slate-500">
              {b.records} records · {b.over_cap_records} over cap · by {b.maker_name ?? b.maker_id}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

function BatchDetail({ id }: { id: number }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const batch = useQuery({ queryKey: ["batch", id], queryFn: () => getBatch(id) });
  const records = useQuery({ queryKey: ["batch-records", id], queryFn: () => getBatchRecords(id) });
  const decide = useMutation({
    mutationFn: (action: "approve" | "reject") => decideBatch(id, action, note.trim()),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["batch", id] });
      qc.invalidateQueries({ queryKey: ["batch-records", id] });
    },
  });

  if (batch.isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (batch.error || !batch.data)
    return <p className="text-sm text-red-700">{t("errors.network")}</p>;
  const b = batch.data;
  const isChecker = user?.role === "INST_CHECKER";
  const pending = b.status === "PENDING_APPROVAL";
  const err =
    decide.error instanceof ApiError
      ? decide.error.message
      : decide.error
        ? t("errors.network")
        : null;

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <h2 className="text-lg font-medium">
          Batch #{b.id} <span className="text-sm font-normal text-slate-500">({b.source})</span>
        </h2>
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
          <dt className="text-slate-500">Status</dt>
          <dd>{b.status}</dd>
          <dt className="text-slate-500">Submitted by</dt>
          <dd>{b.maker_name ?? b.maker_id}</dd>
          <dt className="text-slate-500">Submitted at</dt>
          <dd>{b.submitted_at ? new Date(b.submitted_at).toLocaleString() : "-"}</dd>
          <dt className="text-slate-500">Records</dt>
          <dd>
            {b.records} (
            {Object.entries(b.records_by_status)
              .map(([k, v]) => `${k}: ${v}`)
              .join(", ")}
            )
          </dd>
          <dt className="text-slate-500">Rows excluded (errors)</dt>
          <dd>{b.error_count}</dd>
          {b.justification && (
            <>
              <dt className="text-slate-500">Maker's justification</dt>
              <dd>{b.justification}</dd>
            </>
          )}
          {b.decision_note && (
            <>
              <dt className="text-slate-500">Decision note</dt>
              <dd>{b.decision_note}</dd>
            </>
          )}
          {b.sha256 && (
            <>
              <dt className="text-slate-500">File SHA-256</dt>
              <dd className="break-all font-mono text-xs">{b.sha256}</dd>
            </>
          )}
        </dl>
        {b.over_cap_records > 0 && (
          <p className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
            {b.over_cap_records} {t("approvals.overCap")}
          </p>
        )}
      </div>

      {b.bucket_impact && b.bucket_impact.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-2 font-medium">{t("upload.bucketTitle")}</h3>
          <BucketPanel impact={b.bucket_impact} />
        </div>
      )}

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="mb-2 font-medium">{t("approvals.preview")}</h3>
        {records.isLoading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : (
          <div className="max-h-[28rem] overflow-auto">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-2 py-1">Row</th>
                  <th className="px-2 py-1">APAAR ID</th>
                  <th className="px-2 py-1">Name</th>
                  <th className="px-2 py-1">Programme</th>
                  <th className="px-2 py-1">Bucket</th>
                  <th className="px-2 py-1">Tier</th>
                  <th className="px-2 py-1">Status</th>
                  <th className="px-2 py-1">Findings</th>
                </tr>
              </thead>
              <tbody>
                {records.data?.records.map((r) => (
                  <tr
                    key={r.record_id}
                    className={`border-t border-slate-100 ${r.over_cap ? "bg-amber-50 text-amber-900" : ""}`}
                  >
                    <td className="px-2 py-1 font-mono">{r.row ?? "-"}</td>
                    <td className="px-2 py-1 font-mono text-xs">{r.apaar_id}</td>
                    <td className="px-2 py-1">{r.full_name}</td>
                    <td className="px-2 py-1">
                      {r.programme_code} <span className="text-xs text-slate-500">({r.shift})</span>
                    </td>
                    <td className="px-2 py-1 font-mono text-xs">
                      {r.ledger_key}
                      {r.over_cap && " · flagged"}
                    </td>
                    <td className="px-2 py-1">T{r.trust_tier}</td>
                    <td className="px-2 py-1 text-xs">{r.status}</td>
                    <td
                      className="px-2 py-1 text-xs"
                      title={r.outcomes.map((o) => o.message_en).join("\n")}
                    >
                      {r.outcomes.map((o) => o.code).join(", ") || "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {records.data && records.data.total > records.data.records.length && (
              <p className="p-2 text-xs text-slate-500">
                Showing {records.data.records.length} of {records.data.total}.
              </p>
            )}
          </div>
        )}
      </div>

      {pending && (
        <div className="rounded-lg border border-brand-300 bg-white p-4">
          {!isChecker ? (
            <p className="text-sm text-slate-600">{t("approvals.checkerOnly")}</p>
          ) : b.is_own_batch ? (
            <p
              role="alert"
              className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
            >
              {t("approvals.ownBatch")}
            </p>
          ) : (
            <div className="space-y-3">
              <label htmlFor="note" className="text-sm font-medium text-slate-700">
                {t("approvals.note")}
              </label>
              <textarea
                id="note"
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={t("approvals.notePlaceholder")}
                className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
              />
              {err && (
                <p role="alert" className="text-sm text-red-700">
                  {err}
                </p>
              )}
              <div className="flex gap-3">
                <button
                  type="button"
                  disabled={!b.can_decide || note.trim().length < 3 || decide.isPending}
                  onClick={() => decide.mutate("approve")}
                  className="rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("approvals.approve")}
                </button>
                <button
                  type="button"
                  disabled={!b.can_decide || note.trim().length < 3 || decide.isPending}
                  onClick={() => decide.mutate("reject")}
                  className="rounded border border-slate-400 px-4 py-2 text-sm font-medium hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("approvals.reject")}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {decide.isSuccess && (
        <p role="status" className="text-sm text-emerald-700">
          {t("approvals.decided")}
        </p>
      )}
    </div>
  );
}

export function ApprovalsPage() {
  const [selected, setSelected] = useState<number | null>(null);
  const pending = useQuery({
    queryKey: ["batches", "PENDING_APPROVAL"],
    queryFn: () => listBatches("PENDING_APPROVAL"),
  });
  const all = useQuery({ queryKey: ["batches", "all"], queryFn: () => listBatches() });
  const decided = (all.data ?? []).filter(
    (b) => b.status === "APPROVED" || b.status === "REJECTED",
  );

  return (
    <AppShell area="institution">
      <h1 className="text-2xl font-semibold">{t("approvals.title")}</h1>
      <p className="mt-1 max-w-3xl text-sm text-slate-500">{t("approvals.subtitle")}</p>
      <div className="mt-6 grid gap-6 lg:grid-cols-[22rem_1fr]">
        <div className="space-y-5">
          <section>
            <h2 className="mb-2 font-medium">{t("approvals.pending")}</h2>
            {pending.isLoading ? (
              <p className="text-sm text-slate-500">Loading…</p>
            ) : pending.data?.length ? (
              <BatchList batches={pending.data} selected={selected} onSelect={setSelected} />
            ) : (
              <p className="text-sm text-slate-500">{t("approvals.none")}</p>
            )}
          </section>
          {decided.length > 0 && (
            <section>
              <h2 className="mb-2 font-medium">{t("approvals.history")}</h2>
              <BatchList
                batches={decided.slice(0, 20)}
                selected={selected}
                onSelect={setSelected}
              />
            </section>
          )}
        </div>
        <div>
          {selected ? (
            <BatchDetail id={selected} />
          ) : (
            <p className="text-sm text-slate-500">{t("approvals.select")}</p>
          )}
        </div>
      </div>
    </AppShell>
  );
}
