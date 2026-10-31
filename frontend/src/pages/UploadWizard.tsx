import { Fragment, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { BucketPanel } from "../components/BucketPanel";
import { ApiError } from "../api/client";
import type { Outcome } from "../api/records";
import {
  createUpload,
  downloadTemplate,
  dryRun,
  finalizeUpload,
  getUpload,
  putChunk,
  submitUpload,
  type DryRunResponse,
  type ReportRow,
  type RowSeverity,
  type SubmitResponse,
} from "../api/uploads";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";

const CHUNK_BYTES = 64 * 1024; // small chunks so progress and resume are visible in a demo
const RESUME_PREFIX = "apaar.upload.";

const ROW_STYLE: Record<RowSeverity, string> = {
  ERROR: "bg-red-50 text-red-800",
  WARNING: "bg-amber-50 text-amber-900",
  INFO: "bg-sky-50 text-sky-900",
  OK: "bg-emerald-50 text-emerald-800",
};
const OUTCOME_STYLE: Record<string, string> = {
  ERROR: "border-red-200 bg-red-50 text-red-800",
  WARNING: "border-amber-200 bg-amber-50 text-amber-900",
  INFO: "border-sky-200 bg-sky-50 text-sky-900",
};

type Phase = "idle" | "uploading" | "checking" | "report" | "submitting" | "done";

function fileKey(f: File) {
  return `${RESUME_PREFIX}${f.name}|${f.size}|${f.lastModified}`;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const d = err.body.details as Record<string, unknown>;
    const rows = (d?.rows ?? d?.error_rows) as number[] | undefined;
    return rows?.length ? `${err.message} Rows: ${rows.slice(0, 20).join(", ")}` : err.message;
  }
  return t("errors.network");
}

function Step({
  n,
  title,
  active,
  children,
}: {
  n: number;
  title: string;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={`rounded-lg border bg-white p-5 ${active ? "border-brand-300" : "border-slate-200"}`}
      aria-labelledby={`step-${n}`}
    >
      <h2 id={`step-${n}`} className="flex items-center gap-3 text-lg font-medium">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">
          {n}
        </span>
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function FixText({ o }: { o: Outcome }) {
  const f = o.suggested_fix;
  if (!f) return null;
  return (
    <div className="mt-1 text-xs opacity-90">
      <span className="font-medium">{t("upload.fix")}: </span>
      {f.value && (
        <span>
          {t("upload.useValue")} <code className="rounded bg-white/70 px-1">{f.value}</code>.{" "}
        </span>
      )}
      {f.hint && <span>{f.hint} </span>}
      {f.allowed && <span>Allowed: {f.allowed.join(", ")}. </span>}
      {f.programmes && f.programmes.length > 0 && (
        <span>
          {t("upload.didYouMean")}{" "}
          {f.programmes.map((p) => `${p.programme_code} (${p.shift})`).join(", ")}
        </span>
      )}
    </div>
  );
}

function RowTable({ rows }: { rows: ReportRow[] }) {
  const [filter, setFilter] = useState<RowSeverity | "ALL">("ALL");
  const [open, setOpen] = useState<number | null>(null);
  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: rows.length, ERROR: 0, WARNING: 0, INFO: 0, OK: 0 };
    rows.forEach((r) => (c[r.severity] += 1));
    return c;
  }, [rows]);
  const shown = filter === "ALL" ? rows : rows.filter((r) => r.severity === filter);
  return (
    <div>
      <div role="group" aria-label={t("upload.filter")} className="flex flex-wrap gap-2">
        {(["ALL", "ERROR", "WARNING", "INFO", "OK"] as const).map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            className={`rounded border px-3 py-1 text-sm ${
              filter === f
                ? "border-brand-600 bg-brand-50 text-brand-700"
                : "border-slate-300 hover:bg-slate-100"
            }`}
          >
            {t(`upload.sev.${f}`)} ({counts[f]})
          </button>
        ))}
      </div>
      <div className="mt-3 max-h-[32rem] overflow-auto rounded border border-slate-200">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-2 py-1">Row</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">APAAR ID</th>
              <th className="px-2 py-1">Name</th>
              <th className="px-2 py-1">Programme</th>
              <th className="px-2 py-1">Category</th>
              <th className="px-2 py-1">Bucket</th>
              <th className="px-2 py-1">Findings</th>
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, 1000).map((r) => (
              <Fragment key={r.row}>
                <tr
                  className={`cursor-pointer border-t border-slate-100 ${ROW_STYLE[r.severity]}`}
                  onClick={() => setOpen(open === r.row ? null : r.row)}
                >
                  <td className="px-2 py-1 font-mono">{r.row}</td>
                  <td className="px-2 py-1 text-xs font-semibold">{r.severity}</td>
                  <td className="px-2 py-1 font-mono text-xs">{r.apaar_id ?? "-"}</td>
                  <td className="px-2 py-1">{r.full_name ?? "-"}</td>
                  <td className="px-2 py-1">{r.programme_code ?? "-"}</td>
                  <td className="px-2 py-1">{r.admission_category ?? "-"}</td>
                  <td className="px-2 py-1 font-mono text-xs">{r.ledger_key ?? "-"}</td>
                  <td className="px-2 py-1 text-xs">
                    {r.outcomes.length ? r.outcomes.map((o) => o.code).join(", ") : "-"}
                  </td>
                </tr>
                {open === r.row && r.outcomes.length > 0 && (
                  <tr className="bg-white">
                    <td colSpan={8} className="px-3 py-2">
                      <ul className="space-y-1">
                        {r.outcomes.map((o, i) => (
                          <li
                            key={`${o.code}-${i}`}
                            className={`rounded border px-3 py-2 text-sm ${OUTCOME_STYLE[o.severity]}`}
                          >
                            <span className="mr-2 font-mono text-xs">{o.code}</span>
                            {o.message_en}
                            <FixText o={o} />
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {shown.length > 1000 && (
          <p className="p-2 text-xs text-slate-500">{t("upload.truncated")}</p>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500">{t("upload.rowHint")}</p>
    </div>
  );
}

export function UploadWizardPage() {
  const { user } = useAuth();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<{ done: number; total: number; resumed: number }>({
    done: 0,
    total: 0,
    resumed: 0,
  });
  const [uploadId, setUploadId] = useState<number | null>(null);
  const [report, setReport] = useState<DryRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [excludeErrors, setExcludeErrors] = useState(false);
  const [justification, setJustification] = useState("");
  const [result, setResult] = useState<SubmitResponse | null>(null);
  const isMaker = user?.role === "INST_MAKER";

  async function startUpload() {
    if (!file) return;
    setError(null);
    setReport(null);
    setResult(null);
    setPhase("uploading");
    const total = Math.max(1, Math.ceil(file.size / CHUNK_BYTES));
    try {
      // Resume: an unfinished upload of the same file continues where it stopped.
      let id: number | null = null;
      let have = new Set<number>();
      const saved = localStorage.getItem(fileKey(file));
      if (saved) {
        try {
          const u = await getUpload(Number(saved));
          if (u.stage === "UPLOADING" && u.total_chunks === total) {
            id = u.id;
            have = new Set(u.received_chunks ?? []);
          } else if (u.stage !== "UPLOADING" && u.status === "DRAFT") {
            id = u.id; // finalized, not dry-run yet
            have = new Set(Array.from({ length: total }, (_, i) => i));
          }
        } catch {
          id = null;
        }
      }
      if (id === null) {
        id = (await createUpload(file.name, total)).id;
        localStorage.setItem(fileKey(file), String(id));
      }
      setUploadId(id);
      const resumed = have.size;
      setProgress({ done: have.size, total, resumed });
      for (let i = 0; i < total; i++) {
        if (have.has(i)) continue;
        await putChunk(id, i, file.slice(i * CHUNK_BYTES, (i + 1) * CHUNK_BYTES));
        have.add(i);
        setProgress({ done: have.size, total, resumed });
      }
      await finalizeUpload(id);
      setPhase("checking");
      const dr = await dryRun(id);
      localStorage.removeItem(fileKey(file));
      setReport(dr);
      setExcludeErrors(false);
      setPhase("report");
    } catch (err) {
      setError(errorText(err));
      setPhase("idle");
    }
  }

  async function submit() {
    if (!uploadId || !report) return;
    setError(null);
    setPhase("submitting");
    try {
      const res = await submitUpload(uploadId, {
        exclude_error_rows: excludeErrors,
        justification: justification.trim() || null,
        row_justifications: {},
      });
      setResult(res);
      setPhase("done");
    } catch (err) {
      setError(errorText(err));
      setPhase("report");
    }
  }

  function reset() {
    setFile(null);
    setReport(null);
    setResult(null);
    setUploadId(null);
    setError(null);
    setJustification("");
    setPhase("idle");
    if (fileInput.current) fileInput.current.value = "";
  }

  const s = report?.summary;
  const needsExclude = !!s && s.rows_with_errors > 0;
  const needsJustification = !!s && s.rows_with_warnings > 0;
  const canSubmit =
    isMaker &&
    phase === "report" &&
    !!s &&
    s.rows_submittable > 0 &&
    (!needsExclude || excludeErrors) &&
    (!needsJustification || justification.trim().length >= 3);
  const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <AppShell area="institution">
      <h1 className="text-2xl font-semibold">{t("upload.title")}</h1>
      <p className="mt-1 max-w-3xl text-sm text-slate-500">{t("upload.subtitle")}</p>

      {error && (
        <div
          role="alert"
          className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <div className="mt-6 grid gap-5">
        <Step n={1} title={t("upload.step.template")} active={phase === "idle" && !file}>
          <p className="text-sm text-slate-600">{t("upload.templateNote")}</p>
          <button
            type="button"
            onClick={() => downloadTemplate().catch((e) => setError(errorText(e)))}
            className="mt-3 rounded border border-brand-600 px-4 py-2 text-sm font-medium text-brand-700 hover:bg-brand-50"
          >
            {t("upload.download")}
          </button>
        </Step>

        <Step
          n={2}
          title={t("upload.step.upload")}
          active={phase === "idle" || phase === "uploading"}
        >
          <label htmlFor="csv" className="text-sm font-medium text-slate-700">
            {t("upload.chooseFile")}
          </label>
          <input
            id="csv"
            ref={fileInput}
            type="file"
            accept=".csv,text/csv"
            disabled={phase === "uploading" || phase === "checking" || phase === "submitting"}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block text-sm"
          />
          {file && (
            <p className="mt-2 text-xs text-slate-500">
              {file.name} · {(file.size / 1024).toFixed(1)} KB ·{" "}
              {Math.max(1, Math.ceil(file.size / CHUNK_BYTES))} chunk(s) of {CHUNK_BYTES / 1024} KB
            </p>
          )}
          <button
            type="button"
            disabled={!file || !isMaker || phase === "uploading" || phase === "checking"}
            onClick={startUpload}
            className="mt-3 rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {phase === "uploading" ? t("upload.uploading") : t("upload.start")}
          </button>
          {!isMaker && <p className="mt-2 text-xs text-slate-500">{t("record.makerOnly")}</p>}
          {(phase === "uploading" || progress.total > 0) && (
            <div className="mt-4" aria-live="polite">
              <div
                className="h-3 w-full overflow-hidden rounded bg-slate-100"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct}
                aria-label={t("upload.progress")}
              >
                <div className="h-3 bg-brand-600 transition-all" style={{ width: `${pct}%` }} />
              </div>
              <p className="mt-1 text-xs text-slate-600">
                {progress.done} / {progress.total} chunks ({pct}%)
                {uploadId && ` · upload #${uploadId}`}
                {progress.resumed > 0 && ` · ${t("upload.resumed")} ${progress.resumed}`}
              </p>
              <p className="mt-1 text-xs text-slate-500">{t("upload.resumeNote")}</p>
            </div>
          )}
          {phase === "checking" && (
            <p className="mt-3 text-sm text-slate-600">{t("upload.checking")}</p>
          )}
        </Step>

        <Step n={3} title={t("upload.step.report")} active={phase === "report"}>
          {!report ? (
            <p className="text-sm text-slate-500">{t("upload.reportIdle")}</p>
          ) : (
            <div className="space-y-5">
              <p className="rounded border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                {t("upload.dryRunNote")}
              </p>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                {[
                  ["upload.tile.rows", s!.rows, "text-slate-900"],
                  ["upload.tile.errors", s!.rows_with_errors, "text-red-700"],
                  ["upload.tile.warnings", s!.rows_with_warnings, "text-amber-700"],
                  ["upload.tile.clean", s!.rows_clean + s!.rows_info_only, "text-emerald-700"],
                  ["upload.tile.submittable", s!.rows_submittable, "text-brand-700"],
                ].map(([k, v, tone]) => (
                  <div key={k as string} className="rounded border border-slate-200 p-3">
                    <dt className="text-xs text-slate-500">{t(k as string)}</dt>
                    <dd className={`text-2xl font-semibold ${tone}`}>{v as number}</dd>
                  </div>
                ))}
              </dl>
              {report.header_notes.length > 0 && (
                <ul className="text-xs text-slate-500">
                  {report.header_notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
              <div>
                <h3 className="font-medium">{t("upload.bucketTitle")}</h3>
                <p className="mb-2 text-xs text-slate-500">{t("upload.bucketNote")}</p>
                <BucketPanel impact={report.bucket_impact} />
              </div>
              <div>
                <h3 className="mb-2 font-medium">{t("upload.rowsTitle")}</h3>
                <RowTable rows={report.rows} />
              </div>
            </div>
          )}
        </Step>

        <Step n={4} title={t("upload.step.submit")} active={phase === "report" || phase === "done"}>
          {result ? (
            <div
              role="status"
              className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
            >
              <p className="font-medium">
                {t("upload.submitted")} {result.stored} {t("upload.recordsStored")}
                {result.idempotent_replay && ` (${t("upload.replay")})`}
              </p>
              {result.over_cap_count > 0 && (
                <p className="mt-1 text-amber-900">
                  {result.over_cap_count} {t("upload.flaggedHeld")} (rows{" "}
                  {result.over_cap_rows.slice(0, 20).join(", ")}).
                </p>
              )}
              {result.excluded_error_count > 0 && (
                <p className="mt-1">
                  {result.excluded_error_count} {t("upload.excluded")}
                </p>
              )}
              <p className="mt-1">
                {t("upload.tiers")}{" "}
                {Object.entries(result.trust_tiers)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(", ")}
              </p>
              <div className="mt-3 flex gap-3">
                <Link to="/institution" className="text-brand-700 underline">
                  {t("upload.toDashboard")}
                </Link>
                <button type="button" onClick={reset} className="text-brand-700 underline">
                  {t("upload.another")}
                </button>
              </div>
            </div>
          ) : !report ? (
            <p className="text-sm text-slate-500">{t("upload.submitIdle")}</p>
          ) : (
            <div className="space-y-3">
              {needsExclude && (
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={excludeErrors}
                    onChange={(e) => setExcludeErrors(e.target.checked)}
                    className="mt-1"
                  />
                  <span>
                    {t("upload.excludeLabel").replace("{n}", String(s!.rows_with_errors))}
                  </span>
                </label>
              )}
              {needsJustification && (
                <div>
                  <label htmlFor="justification" className="text-sm font-medium text-slate-700">
                    {t("upload.justification")}
                  </label>
                  <textarea
                    id="justification"
                    value={justification}
                    onChange={(e) => setJustification(e.target.value)}
                    rows={3}
                    className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
                    placeholder={t("upload.justificationHint")}
                  />
                </div>
              )}
              <button
                type="button"
                disabled={!canSubmit}
                onClick={submit}
                className="rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {phase === "submitting" ? t("upload.submitting") : t("upload.submit")}
              </button>
              <p className="text-xs text-slate-500">{t("upload.submitNote")}</p>
            </div>
          )}
        </Step>
      </div>
    </AppShell>
  );
}
