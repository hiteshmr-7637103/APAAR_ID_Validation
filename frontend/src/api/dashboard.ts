import { apiFetch } from "./client";
import type { Batch } from "./uploads";

export interface Bucket {
  ledger_key: string;
  cap: number;
  used: number;
  excess: number;
}

export interface ProgrammeSeats {
  institution_programme_id: number;
  programme_code: string;
  programme_name: string;
  academic_year: string;
  shift: string;
  approved_intake: number;
  declared_enrolment: number | null;
  records_received: number;
  buckets: Bucket[];
}

export interface InstitutionDashboard {
  institution: { id: number; code: string; name: string; state: string };
  academic_year: string;
  seats: { totals: Bucket[]; programmes: ProgrammeSeats[] };
  over_cap: { flagged_records: number; awaiting_aicte_review: number };
  trust_tiers: {
    total: number;
    by_tier: Record<"T0" | "T1" | "T2" | "T3", number>;
    summary_en: string;
    note: string;
  };
  records_by_status: Record<string, number>;
  submissions: { by_status: Record<string, number>; pending_approval: number; recent: Batch[] };
  recent_errors: {
    batch_id: number;
    row: number | null;
    student_record_id: number | null;
    code: string;
    field: string | null;
    message_en: string;
    suggested_fix: { hint?: string } | null;
  }[];
  pending_cases: {
    id: number;
    type: string;
    visibility: string;
    priority: number;
    summary_en: string | null;
    created_at: string;
  }[];
  deferred: Record<string, string>;
}

export function fetchInstitutionDashboard(id: number) {
  return apiFetch<InstitutionDashboard>(`/dashboards/institution/${id}`);
}
