import { apiFetch, apiFetchRaw } from "./client";
import type { Outcome, Severity } from "./records";

export type RowSeverity = Severity | "OK";

export interface BucketImpact {
  institution_programme_id: number;
  programme_code: string;
  academic_year: string;
  shift: string;
  ledger_key: string;
  cap: number;
  used_before: number;
  rows_in_file: number;
  used_after: number;
  excess_after: number;
  over_cap_rows: number[];
  would_be_flagged: boolean;
  message_en: string;
}

export interface DryRunSummary {
  rows: number;
  rows_with_errors: number;
  rows_with_warnings: number;
  rows_info_only: number;
  rows_clean: number;
  rows_submittable: number;
}

export interface ReportRow {
  row: number;
  apaar_id: string | null;
  student_college_id: string | null;
  full_name: string | null;
  programme_code: string | null;
  admission_category: string | null;
  entry_type: string | null;
  ledger_key?: string | null;
  severity: RowSeverity;
  outcomes: Outcome[];
}

export interface DryRunResponse {
  upload_id: number;
  status: string;
  writes_to_repository: boolean;
  summary: DryRunSummary;
  bucket_impact: BucketImpact[];
  header_notes: string[];
  rows: ReportRow[];
}

export interface Batch {
  id: number;
  institution_id: number;
  source: "UPLOAD" | "FORM" | "CORRECTION";
  status: "DRAFT" | "DRY_RUN" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED";
  stage: string;
  filename: string | null;
  academic_year: string | null;
  maker_id: number;
  maker_name: string | null;
  checker_id: number | null;
  row_count: number;
  error_count: number;
  warning_count: number;
  total_chunks: number | null;
  sha256: string | null;
  records: number;
  records_by_status: Record<string, number>;
  over_cap_records: number;
  justification: string | null;
  decision_note: string | null;
  created_at: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  dry_run_summary: DryRunSummary | null;
  submit_result: Record<string, unknown> | null;
  received_chunks?: number[];
  max_chunk_bytes?: number;
  can_decide?: boolean;
  is_own_batch?: boolean;
  bucket_impact?: BucketImpact[];
}

export interface SubmitResponse {
  upload_id: number;
  status: string;
  stored: number;
  excluded_error_rows: number[];
  excluded_error_count: number;
  over_cap_rows: number[];
  over_cap_count: number;
  rows_with_warnings: number;
  trust_tiers: Record<string, number>;
  idempotent_replay: boolean;
}

export interface BatchRecord {
  record_id: number;
  row: number | null;
  apaar_id: string | null;
  student_college_id: string | null;
  full_name: string | null;
  programme_code: string;
  shift: string;
  admission_date: string;
  admission_category: string;
  entry_type: string;
  ledger_key: string;
  over_cap: boolean;
  over_cap_reviewed: boolean;
  trust_tier: number;
  status: string;
  outcomes: {
    code: string;
    severity: Severity;
    message_en: string;
    justification: string | null;
  }[];
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export async function downloadTemplate(): Promise<void> {
  const res = await apiFetchRaw("/templates/students.csv");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "students_template.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export function createUpload(filename: string, totalChunks: number) {
  return apiFetch<Batch>("/uploads", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ filename, total_chunks: totalChunks }),
  });
}

export function getUpload(id: number) {
  return apiFetch<Batch>(`/uploads/${id}`);
}

export function putChunk(id: number, index: number, data: Blob) {
  return apiFetch<{ chunk_index: number; status: string; received_chunks: number[] }>(
    `/uploads/${id}/chunks/${index}`,
    { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: data },
  );
}

export function finalizeUpload(id: number) {
  return apiFetch<{ rows: number; sha256: string; idempotent_replay: boolean }>(
    `/uploads/${id}/finalize`,
    { method: "POST" },
  );
}

export function dryRun(id: number) {
  return apiFetch<DryRunResponse>(`/uploads/${id}/dry-run`, { method: "POST" });
}

export function submitUpload(
  id: number,
  body: {
    exclude_error_rows: boolean;
    justification: string | null;
    row_justifications: Record<number, string>;
  },
) {
  return apiFetch<SubmitResponse>(`/uploads/${id}/submit`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
}

export function listBatches(status?: string) {
  return apiFetch<Batch[]>(`/batches${status ? `?status=${status}` : ""}`);
}

export function getBatch(id: number) {
  return apiFetch<Batch>(`/batches/${id}`);
}

export function getBatchRecords(id: number) {
  return apiFetch<{ total: number; records: BatchRecord[] }>(`/batches/${id}/records?limit=500`);
}

export function decideBatch(id: number, action: "approve" | "reject", note: string) {
  return apiFetch<Record<string, unknown>>(`/batches/${id}/${action}`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ note }),
  });
}
