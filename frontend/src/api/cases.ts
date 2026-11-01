import { apiFetch } from "./client";

export interface CaseItem {
  id: number;
  type: string;
  visibility: "INSTITUTION" | "AICTE_ONLY";
  priority: number;
  severity: "ERROR" | "WARNING" | "INFO";
  status: "OPEN" | "CONFIRMED" | "DISMISSED" | "ESCALATED" | "RESOLVED";
  institution_id: number | null;
  subject_ids_json: Record<string, unknown>;
  score: number | null;
  evidence_json: Record<string, unknown> | null;
  explanation_json: Record<string, unknown> | null;
  decided_by: number | null;
  decided_at: string | null;
  decision_note: string | null;
  created_at: string;
}

export interface CaseDecisionPayload {
  action: string;
  note: string;
  record_ids?: number[];
}

export async function listCases(params?: {
  type?: string;
  status?: string;
  institution_id?: number;
}): Promise<CaseItem[]> {
  const query = new URLSearchParams();
  if (params?.type) query.set("type", params.type);
  if (params?.status) query.set("status", params.status);
  if (params?.institution_id) query.set("institution_id", String(params.institution_id));
  const qs = query.toString();
  return apiFetch<CaseItem[]>(`/cases${qs ? `?${qs}` : ""}`);
}

export async function getCase(id: number): Promise<CaseItem> {
  return apiFetch<CaseItem>(`/cases/${id}`);
}

export async function submitCaseDecision(
  id: number,
  payload: CaseDecisionPayload
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/cases/${id}/decision`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function triggerRetrain(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/demo/retrain", { method: "POST" });
}

export async function triggerTriage(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/demo/triage", { method: "POST" });
}
