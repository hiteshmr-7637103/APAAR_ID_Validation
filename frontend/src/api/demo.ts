import { apiFetch } from "./client";

export interface DemoScenario {
  id: string;
  name: string;
  description: string;
  expected_result: string;
}

export interface ConcurrencyTestResult {
  status: string;
  intake_cap: number;
  initial_used: number;
  seats_remaining_before_race: number;
  concurrent_attempts: number;
  clean_admissions: number;
  flagged_over_cap: number;
  final_used: number;
  excess_reported: number;
  no_lost_updates: boolean;
  elapsed_ms: number;
  throughput_requests_per_sec: number;
}

export interface TamperAuditResult {
  status: string;
  tampered_entry_id: number;
  tamper_detected: boolean;
  first_broken_detected: {
    index: number;
    id: number;
    action: string;
    reason: string;
    expected: string;
    stored_prev_hash: string;
    stored_hash: string;
  } | null;
  repaired_and_restored: boolean;
  chain_valid_now: boolean;
  message: string;
}

export interface ScenarioInjectionResult {
  scenario: string;
  apaar_id?: string;
  record_ids?: number[];
  cases_opened?: number;
  case_ids?: number[];
  is_transfer?: boolean;
  shared_contact_token?: string;
  records_created?: number;
  ring_cases_opened?: number;
  institution_code?: string;
  records_injected?: number;
  anomaly_cases_opened?: number;
  case_id?: number;
  declared_enrolment?: number;
  uploaded_records?: number;
  coverage_pct?: number;
  coverage_band?: string;
  message: string;
}

export async function listDemoScenarios(): Promise<DemoScenario[]> {
  return apiFetch<DemoScenario[]>("/demo/scenarios");
}

export async function injectDemoScenario(scenario: string): Promise<ScenarioInjectionResult> {
  return apiFetch<ScenarioInjectionResult>(`/demo/inject/${scenario}`, {
    method: "POST",
  });
}

export async function runConcurrencyTest(): Promise<ConcurrencyTestResult> {
  return apiFetch<ConcurrencyTestResult>("/demo/concurrency-test", {
    method: "POST",
  });
}

export async function runTamperAudit(): Promise<TamperAuditResult> {
  return apiFetch<TamperAuditResult>("/demo/tamper-audit", {
    method: "POST",
  });
}

export async function seedDemoDataset(
  institutions = 40,
  students = 20000,
  seed = 42
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(
    `/demo/seed?institutions=${institutions}&students=${students}&seed=${seed}`,
    {
      method: "POST",
    }
  );
}

export async function retrainDuplicateModel(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/demo/retrain", {
    method: "POST",
  });
}
