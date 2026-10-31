import { apiFetch } from "./client";

export type Severity = "ERROR" | "WARNING" | "INFO";

export interface ProgrammeSuggestion {
  programme_code: string;
  shift: string;
  name: string;
}

export interface Outcome {
  code: string;
  severity: Severity;
  field: string | null;
  message_key: string;
  message_en: string;
  evidence: Record<string, unknown>;
  suggested_fix: {
    programmes?: ProgrammeSuggestion[];
    hint?: string;
    allowed?: string[];
    value?: string;
  } | null;
}

/** Booleans only: the API never returns MOCK registry contents to institutions. */
export interface ApaarStatus {
  source: string;
  format_ok: boolean;
  suspicious: boolean;
  found: boolean | null;
  name_match: boolean | null;
  dob_match: boolean | null;
}

export interface SeatProjection {
  ledger_key: string;
  cap: number;
  used: number;
  used_after: number;
  excess_after: number;
  would_be_flagged: boolean;
}

export interface ValidationResponse {
  institution_id: number;
  academic_year: string;
  programme: {
    institution_programme_id: number;
    programme_code: string;
    name: string;
    shift: string;
    level: string;
  } | null;
  ledger_key: string | null;
  has_error: boolean;
  counts: Record<Severity, number>;
  outcomes: Outcome[];
  apaar_status: ApaarStatus;
  seat_projection: SeatProjection | null;
}

/** Spec 7.2 columns. Empty strings are sent as null. */
export interface RecordPayload {
  apaar_id: string;
  student_college_id: string;
  full_name: string;
  dob: string;
  gender: string;
  programme_code: string;
  shift: string;
  admission_date: string;
  admission_category: string;
  entry_type: string;
  mode: string;
  nationality: string;
  is_nri: boolean;
  nri_sponsor: string;
  is_oci: boolean;
  oci_number: string;
  is_foreign_national: boolean;
  id_document_note: string;
  phone: string;
  email: string;
  address: string;
}

export interface CreateResponse {
  record: {
    id: number;
    status: string;
    trust_tier: number;
    ledger_key: string;
    over_cap: boolean;
    academic_year: string;
    programme_code: string;
  };
  seat: { ledger_key: string; cap: number; used: number; excess: number; over_cap: boolean };
  outcomes: Outcome[];
  message_en: string;
}

export interface InstitutionProgramme {
  id: number;
  academic_year: string;
  shift: string;
  approved_intake: number;
  programme: { canonical_code: string; name: string; level: string; duration_years: number };
}

function body(p: RecordPayload): string {
  const clean = Object.fromEntries(
    Object.entries(p).map(([k, v]) => [k, typeof v === "string" && v.trim() === "" ? null : v]),
  );
  return JSON.stringify(clean);
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function validateRecord(p: RecordPayload, signal?: AbortSignal) {
  return apiFetch<ValidationResponse>("/validate/record", {
    method: "POST",
    headers: JSON_HEADERS,
    body: body(p),
    signal,
  });
}

export function createRecord(p: RecordPayload) {
  return apiFetch<CreateResponse>("/students", {
    method: "POST",
    headers: JSON_HEADERS,
    body: body(p),
  });
}

export function fetchInstitutionProgrammes(institutionId: number) {
  return apiFetch<InstitutionProgramme[]>(`/institutions/${institutionId}/programmes`);
}
