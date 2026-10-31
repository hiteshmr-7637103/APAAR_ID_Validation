import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { ApiError } from "../api/client";
import {
  createRecord,
  fetchInstitutionProgrammes,
  validateRecord,
  type ApaarStatus,
  type CreateResponse,
  type InstitutionProgramme,
  type Outcome,
  type RecordPayload,
  type SeatProjection,
  type Severity,
  type ValidationResponse,
} from "../api/records";
import { useAuth } from "../hooks/useAuth";
import { t } from "../i18n";

const CURRENT_YEAR = "2025-26";
const CATEGORIES = [
  "GENERAL",
  "SC",
  "ST",
  "OBC",
  "EWS",
  "TFW",
  "NRI",
  "FN",
  "OCI",
  "PIO",
  "CIWGC",
  "OTHER",
];

const EMPTY: RecordPayload = {
  apaar_id: "",
  student_college_id: "",
  full_name: "",
  dob: "",
  gender: "",
  programme_code: "",
  shift: "REGULAR",
  admission_date: "",
  admission_category: "GENERAL",
  entry_type: "REGULAR",
  mode: "FULL_TIME",
  nationality: "Indian",
  is_nri: false,
  nri_sponsor: "",
  is_oci: false,
  oci_number: "",
  is_foreign_national: false,
  id_document_note: "",
  phone: "",
  email: "",
  address: "",
};

const SEVERITY_STYLE: Record<Severity, string> = {
  ERROR: "border-red-200 bg-red-50 text-red-800",
  WARNING: "border-amber-200 bg-amber-50 text-amber-900",
  INFO: "border-sky-200 bg-sky-50 text-sky-900",
};

const INPUT = "mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm";

/** Programme suggestion placeholder: exact code, then prefix, then substring match against
 *  the institution's approved list (ML-4 TF-IDF mapping arrives in Phase 5). */
function matchProgrammes(query: string, rows: InstitutionProgramme[]): InstitutionProgramme[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  const rank = (p: InstitutionProgramme) => {
    const code = p.programme.canonical_code.toLowerCase();
    const name = p.programme.name.toLowerCase();
    if (code === q) return 0;
    if (code.startsWith(q) || name.startsWith(q)) return 1;
    if (code.includes(q) || name.includes(q)) return 2;
    return 9;
  };
  return rows
    .map((p) => [rank(p), p] as const)
    .filter(([r]) => r < 9)
    .sort((a, b) => a[0] - b[0])
    .map(([, p]) => p);
}

function FieldMessages({ outcomes }: { outcomes: Outcome[] }) {
  if (!outcomes.length) return null;
  return (
    <ul className="mt-1 space-y-1">
      {outcomes.map((o, i) => (
        <li
          key={`${o.code}-${i}`}
          className={`rounded border px-2 py-1 text-xs ${SEVERITY_STYLE[o.severity]}`}
        >
          {o.message_en}
        </li>
      ))}
    </ul>
  );
}

function Field(props: {
  id: keyof RecordPayload;
  label: string;
  outcomes: Outcome[];
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div>
      <label htmlFor={props.id} className="text-sm font-medium text-slate-700">
        {props.label}
      </label>
      {props.children}
      {props.hint && <p className="mt-1 text-xs text-slate-500">{props.hint}</p>}
      <FieldMessages outcomes={props.outcomes} />
    </div>
  );
}

function ApaarStatusPanel({ status, apaar }: { status?: ApaarStatus; apaar: string }) {
  let tone = "border-slate-200 bg-slate-50 text-slate-600";
  let text = t("record.apaar.idle");
  if (apaar && status) {
    if (!status.format_ok) {
      tone = "border-red-200 bg-red-50 text-red-800";
      text = t("record.apaar.invalid");
    } else if (status.found === false) {
      tone = "border-red-200 bg-red-50 text-red-800";
      text = t("record.apaar.notFound");
    } else if (status.found && status.name_match !== false && status.dob_match !== false) {
      tone = "border-emerald-200 bg-emerald-50 text-emerald-800";
      text = t("record.apaar.match");
    } else if (status.found) {
      tone = "border-amber-200 bg-amber-50 text-amber-900";
      const what = [
        status.name_match === false ? t("record.apaar.name") : null,
        status.dob_match === false ? t("record.apaar.dob") : null,
      ].filter(Boolean);
      text = `${t("record.apaar.mismatch")} ${what.join(" and ")}.`;
    }
  }
  return (
    <section aria-live="polite" className={`rounded-lg border p-4 text-sm ${tone}`}>
      <h3 className="font-medium">{t("record.apaar.title")}</h3>
      <p className="mt-1">{text}</p>
      {status?.suspicious && <p className="mt-1">{t("record.apaar.suspicious")}</p>}
      <p className="mt-2 text-xs opacity-75">{t("record.apaar.mockNote")}</p>
    </section>
  );
}

function SeatPanel({ seat }: { seat: SeatProjection | null | undefined }) {
  if (!seat) {
    return (
      <section className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        <h3 className="font-medium">{t("record.seat.title")}</h3>
        <p className="mt-1">{t("record.seat.idle")}</p>
      </section>
    );
  }
  const over = seat.would_be_flagged;
  return (
    <section
      aria-live="polite"
      className={`rounded-lg border p-4 text-sm ${
        over
          ? "border-amber-200 bg-amber-50 text-amber-900"
          : "border-emerald-200 bg-emerald-50 text-emerald-800"
      }`}
    >
      <h3 className="font-medium">
        {t("record.seat.title")}: <span className="font-mono">{seat.ledger_key}</span>
      </h3>
      <p className="mt-1">
        {seat.used} of {seat.cap} seats used. This admission would be seat {seat.used_after}
        {over ? `, ${seat.excess_after} over the cap.` : ", within the cap."}
      </p>
      {over && <p className="mt-1">{t("record.seat.flagged")}</p>}
    </section>
  );
}

function OutcomeList({ outcomes }: { outcomes: Outcome[] }) {
  const groups: Severity[] = ["ERROR", "WARNING", "INFO"];
  if (!outcomes.length) {
    return <p className="text-sm text-emerald-700">{t("record.checks.clean")}</p>;
  }
  return (
    <div className="space-y-3">
      {groups.map((sev) => {
        const items = outcomes.filter((o) => o.severity === sev);
        if (!items.length) return null;
        return (
          <div key={sev}>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {t(`record.severity.${sev}`)} ({items.length})
            </h4>
            <ul className="mt-1 space-y-1">
              {items.map((o, i) => (
                <li
                  key={`${o.code}-${i}`}
                  className={`rounded border px-3 py-2 text-sm ${SEVERITY_STYLE[sev]}`}
                >
                  <span className="mr-2 font-mono text-xs">{o.code}</span>
                  {o.message_en}
                  {o.suggested_fix?.hint && (
                    <div className="mt-1 text-xs opacity-80">{o.suggested_fix.hint}</div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export function StudentRecordFormPage() {
  const { user } = useAuth();
  const institutionId = user?.institution?.id;
  const [form, setForm] = useState<RecordPayload>(EMPTY);
  const [touched, setTouched] = useState(false);
  const [programmeQuery, setProgrammeQuery] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [validatedFor, setValidatedFor] = useState<RecordPayload | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [saved, setSaved] = useState<CreateResponse | null>(null);

  const programmes = useQuery({
    queryKey: ["institution-programmes", institutionId],
    queryFn: () => fetchInstitutionProgrammes(institutionId!),
    enabled: !!institutionId,
  });
  const currentProgrammes = useMemo(
    () => (programmes.data ?? []).filter((p) => p.academic_year === CURRENT_YEAR),
    [programmes.data],
  );
  const suggestions = useMemo(
    () => matchProgrammes(programmeQuery, currentProgrammes).slice(0, 6),
    [programmeQuery, currentProgrammes],
  );

  // Live validation: debounced dry run of the rule engine (nothing is written).
  useEffect(() => {
    if (!touched) return;
    const ctrl = new AbortController();
    const timer = window.setTimeout(async () => {
      setChecking(true);
      setCheckError(null);
      try {
        setValidation(await validateRecord(form, ctrl.signal));
        setValidatedFor(form);
      } catch (err) {
        if (ctrl.signal.aborted) return;
        setCheckError(err instanceof ApiError ? err.message : t("errors.network"));
      } finally {
        if (!ctrl.signal.aborted) setChecking(false);
      }
    }, 450);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [form, touched]);

  const create = useMutation({
    mutationFn: createRecord,
    onSuccess: (res) => setSaved(res),
  });

  function set<K extends keyof RecordPayload>(key: K, value: RecordPayload[K]) {
    setTouched(true);
    setSaved(null);
    create.reset();
    setForm((f) => ({ ...f, [key]: value }));
  }

  function pickProgramme(code: string, shift: string, name: string) {
    setProgrammeQuery(`${code} · ${name}`);
    setShowSuggestions(false);
    setTouched(true);
    setSaved(null);
    setForm((f) => ({ ...f, programme_code: code, shift }));
  }

  const of = (field: string) => validation?.outcomes.filter((o) => o.field === field) ?? [];
  const category = form.admission_category;
  const needsSponsor = category === "NRI" || form.is_nri;
  const needsOci = category === "OCI" || form.is_oci;
  const isForeign = category === "FN" || form.is_foreign_national;
  const isMaker = user?.role === "INST_MAKER";
  // Results describe `validatedFor`; while the user is still typing they are stale.
  const stale = touched && validatedFor !== form;
  const canSubmit = isMaker && !!validation && !validation.has_error && !stale && !create.isPending;
  const serverSuggestions =
    validation?.outcomes.find((o) => o.code === "PROGRAMME_NOT_APPROVED")?.suggested_fix
      ?.programmes ?? [];
  const submitErrors =
    create.error instanceof ApiError
      ? ((create.error.body.details?.outcomes as Outcome[] | undefined) ?? [])
      : [];

  return (
    <AppShell area="institution">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">{t("record.title")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("record.subtitle")}</p>
        </div>
        <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
          {t("record.academicYear")}: {CURRENT_YEAR}
        </span>
      </div>

      {saved && (
        <div
          role="status"
          className={`mt-6 rounded-lg border p-4 text-sm ${
            saved.record.over_cap
              ? "border-amber-200 bg-amber-50 text-amber-900"
              : "border-emerald-200 bg-emerald-50 text-emerald-800"
          }`}
        >
          <p className="font-medium">{saved.message_en}</p>
          <p className="mt-1">
            Record #{saved.record.id} · {saved.record.programme_code} · status {saved.record.status}{" "}
            · trust tier T{saved.record.trust_tier} · bucket {saved.record.ledger_key} (
            {saved.seat.used}/{saved.seat.cap})
          </p>
          <button
            type="button"
            className="mt-3 rounded border border-current px-3 py-1.5"
            onClick={() => {
              setForm(EMPTY);
              setProgrammeQuery("");
              setValidation(null);
              setValidatedFor(null);
              setTouched(false);
              setSaved(null);
              create.reset();
            }}
          >
            {t("record.another")}
          </button>
        </div>
      )}

      <div className="mt-6 grid gap-8 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <form
          className="space-y-6"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit) create.mutate(form);
          }}
        >
          <fieldset className="grid gap-4 rounded-lg border border-slate-200 bg-white p-6 md:grid-cols-2">
            <legend className="px-1 text-sm font-semibold text-slate-700">
              {t("record.section.identity")}
            </legend>
            <Field id="apaar_id" label={t("record.apaar_id")} outcomes={of("apaar_id")}>
              <input
                id="apaar_id"
                inputMode="numeric"
                maxLength={14}
                className={`${INPUT} font-mono`}
                value={form.apaar_id}
                onChange={(e) => set("apaar_id", e.target.value.replace(/\s/g, ""))}
              />
            </Field>
            <Field
              id="student_college_id"
              label={t("record.student_college_id")}
              outcomes={of("student_college_id")}
            >
              <input
                id="student_college_id"
                className={INPUT}
                value={form.student_college_id}
                onChange={(e) => set("student_college_id", e.target.value)}
              />
            </Field>
            <Field id="full_name" label={t("record.full_name")} outcomes={of("full_name")}>
              <input
                id="full_name"
                className={INPUT}
                value={form.full_name}
                onChange={(e) => set("full_name", e.target.value)}
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field id="dob" label={t("record.dob")} outcomes={of("dob")}>
                <input
                  id="dob"
                  type="date"
                  className={INPUT}
                  value={form.dob}
                  onChange={(e) => set("dob", e.target.value)}
                />
              </Field>
              <Field id="gender" label={t("record.gender")} outcomes={of("gender")}>
                <select
                  id="gender"
                  className={INPUT}
                  value={form.gender}
                  onChange={(e) => set("gender", e.target.value)}
                >
                  <option value="">{t("record.choose")}</option>
                  <option value="F">F</option>
                  <option value="M">M</option>
                  <option value="O">O</option>
                </select>
              </Field>
            </div>
          </fieldset>

          <fieldset className="grid gap-4 rounded-lg border border-slate-200 bg-white p-6 md:grid-cols-2">
            <legend className="px-1 text-sm font-semibold text-slate-700">
              {t("record.section.admission")}
            </legend>
            <div className="relative md:col-span-2">
              <Field
                id="programme_code"
                label={t("record.programme")}
                outcomes={of("programme_code")}
                hint={t("record.programme.hint")}
              >
                <input
                  id="programme_code"
                  role="combobox"
                  aria-expanded={showSuggestions}
                  aria-controls="programme-suggestions"
                  autoComplete="off"
                  className={INPUT}
                  value={programmeQuery}
                  onFocus={() => setShowSuggestions(true)}
                  onBlur={() => window.setTimeout(() => setShowSuggestions(false), 150)}
                  onChange={(e) => {
                    const q = e.target.value;
                    setProgrammeQuery(q);
                    setShowSuggestions(true);
                    set("programme_code", q.trim().toUpperCase());
                  }}
                />
              </Field>
              {showSuggestions && suggestions.length > 0 && (
                <ul
                  id="programme-suggestions"
                  role="listbox"
                  className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded border border-slate-200 bg-white shadow"
                >
                  {suggestions.map((p) => (
                    <li
                      key={p.id}
                      role="option"
                      aria-selected={
                        form.programme_code === p.programme.canonical_code && form.shift === p.shift
                      }
                    >
                      <button
                        type="button"
                        className="flex w-full justify-between px-3 py-2 text-left text-sm hover:bg-brand-50"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() =>
                          pickProgramme(p.programme.canonical_code, p.shift, p.programme.name)
                        }
                      >
                        <span>
                          <span className="font-mono">{p.programme.canonical_code}</span>{" "}
                          {p.programme.name}
                        </span>
                        <span className="text-xs text-slate-500">
                          {p.shift} · intake {p.approved_intake}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {serverSuggestions.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-slate-500">{t("record.programme.didYouMean")}</span>
                  {serverSuggestions.map((s) => (
                    <button
                      key={`${s.programme_code}-${s.shift}`}
                      type="button"
                      className="rounded border border-brand-600 px-2 py-1 text-brand-700 hover:bg-brand-50"
                      onClick={() => pickProgramme(s.programme_code, s.shift, s.name)}
                    >
                      {s.programme_code} ({s.shift})
                    </button>
                  ))}
                </div>
              )}
              {form.programme_code && (
                <p className="mt-1 text-xs text-slate-500">
                  {t("record.shift")}: {form.shift}
                </p>
              )}
            </div>
            <Field
              id="admission_date"
              label={t("record.admission_date")}
              outcomes={of("admission_date")}
            >
              <input
                id="admission_date"
                type="date"
                className={INPUT}
                value={form.admission_date}
                onChange={(e) => set("admission_date", e.target.value)}
              />
            </Field>
            <Field
              id="admission_category"
              label={t("record.admission_category")}
              outcomes={of("admission_category")}
            >
              <select
                id="admission_category"
                className={INPUT}
                value={form.admission_category}
                onChange={(e) => set("admission_category", e.target.value)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="entry_type" label={t("record.entry_type")} outcomes={of("entry_type")}>
              <select
                id="entry_type"
                className={INPUT}
                value={form.entry_type}
                onChange={(e) => set("entry_type", e.target.value)}
              >
                <option value="REGULAR">REGULAR</option>
                <option value="LATERAL">LATERAL</option>
              </select>
            </Field>
            <Field id="mode" label={t("record.mode")} outcomes={of("mode")}>
              <select
                id="mode"
                className={INPUT}
                value={form.mode}
                onChange={(e) => set("mode", e.target.value)}
              >
                <option value="FULL_TIME">FULL_TIME</option>
                <option value="PART_TIME">PART_TIME</option>
              </select>
            </Field>
          </fieldset>

          <fieldset className="grid gap-4 rounded-lg border border-slate-200 bg-white p-6 md:grid-cols-2">
            <legend className="px-1 text-sm font-semibold text-slate-700">
              {t("record.section.status")}
            </legend>
            <Field id="nationality" label={t("record.nationality")} outcomes={of("nationality")}>
              <input
                id="nationality"
                className={INPUT}
                value={form.nationality}
                onChange={(e) => set("nationality", e.target.value)}
              />
            </Field>
            <div className="flex flex-wrap items-end gap-4 text-sm">
              {(
                [
                  ["is_nri", "record.is_nri"],
                  ["is_oci", "record.is_oci"],
                  ["is_foreign_national", "record.is_foreign_national"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form[key]}
                    onChange={(e) => set(key, e.target.checked)}
                  />
                  {t(label)}
                </label>
              ))}
            </div>
            {needsSponsor && (
              <Field id="nri_sponsor" label={t("record.nri_sponsor")} outcomes={of("nri_sponsor")}>
                <input
                  id="nri_sponsor"
                  className={INPUT}
                  value={form.nri_sponsor}
                  onChange={(e) => set("nri_sponsor", e.target.value)}
                />
              </Field>
            )}
            {needsOci && (
              <Field id="oci_number" label={t("record.oci_number")} outcomes={of("oci_number")}>
                <input
                  id="oci_number"
                  className={INPUT}
                  value={form.oci_number}
                  onChange={(e) => set("oci_number", e.target.value)}
                />
              </Field>
            )}
            {isForeign && (
              <Field
                id="id_document_note"
                label={t("record.id_document_note")}
                outcomes={of("id_document_note")}
              >
                <input
                  id="id_document_note"
                  className={INPUT}
                  value={form.id_document_note}
                  onChange={(e) => set("id_document_note", e.target.value)}
                />
              </Field>
            )}
          </fieldset>

          <fieldset className="grid gap-4 rounded-lg border border-slate-200 bg-white p-6 md:grid-cols-3">
            <legend className="px-1 text-sm font-semibold text-slate-700">
              {t("record.section.contact")}
            </legend>
            {(["phone", "email", "address"] as const).map((key) => (
              <Field key={key} id={key} label={t(`record.${key}`)} outcomes={of(key)}>
                <input
                  id={key}
                  type={key === "email" ? "email" : "text"}
                  className={INPUT}
                  value={form[key]}
                  onChange={(e) => set(key, e.target.value)}
                />
              </Field>
            ))}
            <p className="text-xs text-slate-500 md:col-span-3">{t("record.contact.note")}</p>
          </fieldset>

          {submitErrors.length > 0 && (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4">
              <p className="mb-2 text-sm font-medium text-red-800">{create.error?.message}</p>
              <OutcomeList outcomes={submitErrors} />
            </div>
          )}
          {create.error && !submitErrors.length && (
            <p role="alert" className="text-sm text-red-700">
              {create.error.message}
            </p>
          )}

          <div className="flex items-center gap-4">
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {create.isPending ? t("record.saving") : t("record.save")}
            </button>
            <span className="text-sm text-slate-500">
              {!isMaker
                ? t("record.makerOnly")
                : validation?.has_error
                  ? t("record.fixErrors")
                  : t("record.saveNote")}
            </span>
          </div>
        </form>

        <aside className="space-y-4 xl:sticky xl:top-6 xl:self-start">
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-medium">{t("record.checks.title")}</h2>
              <span aria-live="polite" className="text-xs text-slate-500">
                {checking || (stale && !checkError)
                  ? t("record.checks.running")
                  : validation
                    ? t("record.checks.done")
                    : ""}
              </span>
            </div>
            <div className="mt-3">
              {checkError ? (
                <p className="text-sm text-red-700">{checkError}</p>
              ) : validation ? (
                <OutcomeList outcomes={validation.outcomes} />
              ) : (
                <p className="text-sm text-slate-500">{t("record.checks.idle")}</p>
              )}
            </div>
          </section>
          <ApaarStatusPanel status={validation?.apaar_status} apaar={form.apaar_id} />
          <SeatPanel seat={validation?.seat_projection} />
        </aside>
      </div>
    </AppShell>
  );
}
