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

export interface ProgrammeCoverage {
  institution_programme_id: number;
  programme_code: string;
  programme_name: string;
  shift: string;
  approved_intake: number;
  declared_enrolment: number;
  received_records: number;
  coverage_percentage: number;
  status_band: "RED" | "AMBER" | "GREEN";
}

export interface InstitutionCoverage {
  institution_id: number;
  institution_code: string;
  institution_name: string;
  academic_year: string;
  declared_headcount: number;
  received_records: number;
  coverage_percentage: number;
  status_band: "RED" | "AMBER" | "GREEN";
  programmes: ProgrammeCoverage[];
}

export interface HealthScoreComponent {
  score: number;
  pct: number;
}

export interface DataHealthScore {
  institution_id: number;
  academic_year: string;
  overall_score: number;
  rating: "EXCELLENT" | "GOOD" | "NEEDS_ATTENTION" | "POOR";
  components: {
    completeness: HealthScoreComponent;
    validity: HealthScoreComponent;
    timeliness: HealthScoreComponent;
    clean_share: HealthScoreComponent;
  };
  summary_en: string;
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
  coverage_meter?: InstitutionCoverage;
  data_health_score?: DataHealthScore;
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
}

export interface RiskRankingItem {
  institution_id: number;
  institution_code: string;
  institution_name: string;
  state: string;
  risk_score: number;
  rank: number;
  components: {
    anomaly: { value: number; weighted: number };
    duplicate_rate: { value: number; weighted: number };
    ring_flags: { value: number; weighted: number };
    rule_violations: { value: number; weighted: number };
  };
  alerts: string[];
}

export interface AicteDashboard {
  academic_year: string;
  state: string | null;
  total_institutions: number;
  total_students: number;
  enrolment_vs_intake: {
    approved_intake: number;
    actual_enrolment: number;
    fill_ratio: number;
  };
  state_breakdown: {
    state: string;
    institutions_count: number;
    approved_intake: number;
    actual_enrolment: number;
    fill_ratio: number;
  }[];
  over_cap: {
    flagged_records: number;
    total_excess_seats: number;
  };
  coverage: {
    total_declared: number;
    total_received: number;
    overall_percentage: number;
    status_band: "RED" | "AMBER" | "GREEN";
    bands: { RED: number; AMBER: number; GREEN: number };
  };
  trust_tiers: {
    total: number;
    T0: number;
    T1: number;
    T2: number;
    T3: number;
    trust_ratio_t3: number;
  };
  cases: {
    total_open: number;
    by_type: Record<string, number>;
    cross_institution_duplicates: number;
  };
  risk_leaderboard: RiskRankingItem[];
}

export function fetchInstitutionDashboard(id: number) {
  return apiFetch<InstitutionDashboard>(`/dashboards/institution/${id}`);
}

export function fetchAicteDashboard(academicYear?: string, state?: string) {
  const params = new URLSearchParams();
  if (academicYear) params.set("academic_year", academicYear);
  if (state) params.set("state", state);
  const q = params.toString();
  return apiFetch<AicteDashboard>(`/dashboards/aicte${q ? `?${q}` : ""}`);
}

export function fetchRiskRanking(academicYear?: string, state?: string) {
  const params = new URLSearchParams();
  if (academicYear) params.set("academic_year", academicYear);
  if (state) params.set("state", state);
  const q = params.toString();
  return apiFetch<RiskRankingItem[]>(`/dashboards/institutions/risk-ranking${q ? `?${q}` : ""}`);
}
