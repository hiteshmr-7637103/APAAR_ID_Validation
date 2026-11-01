import { apiFetch } from "./client";

export interface QuotaRuleItem {
  key: string;
  description: string;
  active_value: Record<string, unknown>;
  active_version: number | null;
  is_default: boolean;
  history?: Array<{
    version: number;
    value_json: Record<string, unknown>;
    description: string;
    effective_from: string;
  }>;
}

export async function listQuotaRules(): Promise<QuotaRuleItem[]> {
  return apiFetch<QuotaRuleItem[]>("/rules");
}

export async function updateQuotaRule(
  key: string,
  body: { value_json: Record<string, unknown>; description?: string }
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/rules/${key}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
