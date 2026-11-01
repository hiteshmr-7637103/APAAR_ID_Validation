import { apiFetch, API_BASE } from "./client";

export interface EnrolmentItem {
  confirmation_id: number;
  record_id: number;
  apaar_id: string;
  institution_name: string;
  institution_code: string;
  programme_name: string;
  programme_code: string;
  academic_year: string;
  admission_date: string | null;
  trust_tier: number;
  status: string;
  confirmation_status: "PENDING" | "CONFIRMED" | "DISPUTED" | "EXPIRED";
  requested_at: string;
  expires_at: string | null;
  decided_at: string | null;
}

export interface ConsentRequestItem {
  id: number;
  student_apaar_id: string | null;
  verifier_name: string;
  verifier_email: string;
  purpose: string;
  requested_fields: string[];
  granted_fields: string[] | null;
  status: "PENDING" | "GRANTED" | "DENIED" | "REVOKED" | "EXPIRED";
  created_at: string;
  expires_at: string | null;
  decided_at: string | null;
}

export interface AccessLogItem {
  id: number;
  verifier_name: string;
  verifier_email: string;
  purpose: string;
  fields_shared: string[];
  outcome: string;
  timestamp: string;
}

export interface VerifiedCredential {
  credential: {
    key_id: string;
    issued_at: string;
    expires_at: string;
    nonce: string;
    consent_id: number;
    purpose: string;
    apaar_id_suffix: string;
    disclosed_fields: Record<string, string | number | null>;
  };
  signature: string;
  public_key: string;
  record_hash: string;
  qr_payload: Record<string, unknown>;
  qr_string: string;
}

export interface CertificateData {
  record_id: number;
  apaar_id: string;
  student_college_id: string | null;
  student_name: string;
  institution_code: string;
  institution_name: string;
  programme_name: string;
  programme_code: string;
  trust_tier: number;
  admission_date: string;
  issued_at: string;
  key_id: string;
  signature: string;
  public_key: string;
  qr_payload: Record<string, unknown>;
  qr_string: string;
}

export interface OfflineVerifyResult {
  outcome: "VALID" | "INVALID" | "EXPIRED" | "REVOKED";
  reason?: string;
  key_id?: string;
  status?: string;
  trust_tier?: number;
  apaar_suffix?: string;
  institution_code?: string;
  programme_code?: string;
  disclosed_fields?: Record<string, unknown>;
  purpose?: string;
}

// Student APIs
export function fetchMyEnrolments() {
  return apiFetch<EnrolmentItem[]>("/students/me/enrolments");
}

export function confirmEnrolment(confirmationId: number) {
  return apiFetch<{ status: string; trust_tier: number; message_en: string }>(
    `/enrolments/${confirmationId}/confirm`,
    { method: "POST" }
  );
}

export function disputeEnrolment(confirmationId: number, reason: string) {
  return apiFetch<{ status: string; case_id: number; message_en: string }>(
    `/enrolments/${confirmationId}/dispute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }
  );
}

export function fetchAccessLog() {
  return apiFetch<AccessLogItem[]>("/students/me/access-log");
}

// Consent APIs
export function fetchConsentRequests() {
  return apiFetch<ConsentRequestItem[]>("/consent/requests");
}

export function grantConsent(consentId: number, grantedFields: string[]) {
  return apiFetch<{ status: string; consent_id: number }>(
    `/consent/${consentId}/grant`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ granted_fields: grantedFields }),
    }
  );
}

export function denyConsent(consentId: number) {
  return apiFetch<{ status: string; consent_id: number }>(
    `/consent/${consentId}/deny`,
    { method: "POST" }
  );
}

export function revokeConsent(consentId: number) {
  return apiFetch<{ status: string; consent_id: number }>(
    `/consent/${consentId}/revoke`,
    { method: "POST" }
  );
}

// Verifier APIs
export function submitVerificationRequest(
  apaarId: string,
  purpose: string,
  requestedFields: string[]
) {
  return apiFetch<{ consent_id: number; status: string; message_en: string }>(
    "/verify/requests",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        apaar_id: apaarId,
        purpose,
        requested_fields: requestedFields,
      }),
    }
  );
}

export function fetchVerifiedRecord(consentId: number) {
  return apiFetch<VerifiedCredential>(`/verify/records/${consentId}`);
}

// Public Verification APIs
export async function verifyOffline(payload: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/verify/offline`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  return (await res.json()) as OfflineVerifyResult;
}

export async function fetchPublicKey() {
  const res = await fetch(`${API_BASE}/.well-known/public-key`);
  return (await res.json()) as { key_id: string; algorithm: string; public_key_hex: string };
}

export function fetchCertificate(recordId: number) {
  return apiFetch<CertificateData>(`/certificates/${recordId}`);
}
