import { apiFetch } from "./client";

export interface AuditEntry {
  id: number;
  ts: string;
  actor_id: number | null;
  actor_role: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  detail: Record<string, unknown> | null;
  prev_hash: string | null;
  hash: string;
}

export interface AuditVerify {
  valid: boolean;
  entries?: number;
  entries_checked?: number;
  head_hash?: string | null;
  first_broken: null | {
    index: number;
    id: number;
    action: string;
    reason: string;
    expected: string;
    stored_hash: string;
  };
  checked_at: string;
}

export function listAudit(action?: string) {
  const q = new URLSearchParams({ limit: "200" });
  if (action) q.set("action", action);
  return apiFetch<{ total: number; entries: AuditEntry[] }>(`/audit?${q.toString()}`);
}

export function verifyAudit() {
  return apiFetch<AuditVerify>("/audit/verify");
}
