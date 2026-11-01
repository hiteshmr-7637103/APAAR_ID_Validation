import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { useAuth } from "../hooks/useAuth";
import {
  fetchMyEnrolments,
  confirmEnrolment,
  disputeEnrolment,
  fetchConsentRequests,
  grantConsent,
  denyConsent,
  revokeConsent,
  fetchAccessLog,
  type EnrolmentItem,
  type ConsentRequestItem,
  type AccessLogItem,
} from "../api/verification";

const FIELD_LABELS: Record<string, string> = {
  student_name: "Student Name",
  dob: "Date of Birth",
  gender: "Gender",
  institution_name: "Institution Name",
  institution_code: "Institution Code",
  programme_name: "Programme Name",
  programme_code: "Programme Code",
  academic_year: "Academic Year",
  admission_date: "Admission Date",
  student_college_id: "College Roll / Registration No.",
  status: "Enrolment Status",
  trust_tier: "Verified Trust Tier",
};

export function StudentPortalPage() {
  const { user } = useAuth();
  const [enrolments, setEnrolments] = useState<EnrolmentItem[]>([]);
  const [consentRequests, setConsentRequests] = useState<ConsentRequestItem[]>([]);
  const [accessLogs, setAccessLogs] = useState<AccessLogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Dispute modal / input state
  const [disputeId, setDisputeId] = useState<number | null>(null);
  const [disputeReason, setDisputeReason] = useState("");
  const [disputing, setDisputing] = useState(false);

  // Selected checkboxes for selective disclosure per consent request
  const [selectedFields, setSelectedFields] = useState<Record<number, string[]>>({});

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [enr, con, logs] = await Promise.all([
        fetchMyEnrolments(),
        fetchConsentRequests(),
        fetchAccessLog(),
      ]);
      setEnrolments(enr);
      setConsentRequests(con);
      setAccessLogs(logs);

      // Initialize selected fields with all requested fields by default
      const initialFields: Record<number, string[]> = {};
      con.forEach((c) => {
        if (c.status === "PENDING") {
          initialFields[c.id] = [...c.requested_fields];
        }
      });
      setSelectedFields(initialFields);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Error loading data: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleConfirm = async (confirmationId: number) => {
    try {
      const res = await confirmEnrolment(confirmationId);
      setActionMessage(`Enrolment confirmed! Trust tier raised to T${res.trust_tier}.`);
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Confirmation failed: ${msg}`);
    }
  };

  const handleDisputeSubmit = async () => {
    if (!disputeId || !disputeReason.trim()) return;
    setDisputing(true);
    try {
      await disputeEnrolment(disputeId, disputeReason);
      setActionMessage("Dispute submitted. Held for AICTE regulatory investigation.");
      setDisputeId(null);
      setDisputeReason("");
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Dispute failed: ${msg}`);
    } finally {
      setDisputing(false);
    }
  };

  const handleToggleField = (consentId: number, field: string) => {
    const current = selectedFields[consentId] || [];
    const next = current.includes(field)
      ? current.filter((f) => f !== field)
      : [...current, field];
    setSelectedFields({ ...selectedFields, [consentId]: next });
  };

  const handleGrant = async (consentId: number) => {
    const granted = selectedFields[consentId] || [];
    if (granted.length === 0) {
      alert("Please select at least one field to grant.");
      return;
    }
    try {
      await grantConsent(consentId, granted);
      setActionMessage("Consent granted with selective disclosure. Signed credential generated.");
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Grant failed: ${msg}`);
    }
  };

  const handleDeny = async (consentId: number) => {
    try {
      await denyConsent(consentId);
      setActionMessage("Consent request denied.");
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Deny failed: ${msg}`);
    }
  };

  const handleRevoke = async (consentId: number) => {
    if (!confirm("Are you sure you want to revoke this consent? Any issued credentials will be invalidated.")) {
      return;
    }
    try {
      await revokeConsent(consentId);
      setActionMessage("Consent revoked. Credential added to revocation registry.");
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Revocation failed: ${msg}`);
    }
  };

  const tierBadge = (tier: number) => {
    switch (tier) {
      case 3:
        return "bg-emerald-100 text-emerald-800 border-emerald-300";
      case 2:
        return "bg-indigo-100 text-indigo-800 border-indigo-300";
      case 1:
        return "bg-blue-100 text-blue-800 border-blue-300";
      default:
        return "bg-slate-100 text-slate-700 border-slate-300";
    }
  };

  return (
    <AppShell area="student">
      <div className="space-y-6">
        {/* Header */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Student Portal & Consent Center</h1>
            <p className="text-sm text-slate-500 mt-1">
              Verify your institutional enrolments (T3 confirmation), manage verification consent, and inspect access logs.
            </p>
          </div>
          {user?.student_apaar_id && (
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg text-right">
              <span className="text-xs text-slate-500 block">Linked APAAR ID</span>
              <span className="text-base font-mono font-bold text-indigo-600">
                {user.student_apaar_id}
              </span>
            </div>
          )}
        </div>

        {actionMessage && (
          <div className="bg-indigo-50 border border-indigo-200 text-indigo-800 p-4 rounded-md text-sm flex justify-between items-center">
            <span>{actionMessage}</span>
            <button
              type="button"
              onClick={() => setActionMessage(null)}
              className="text-xs font-bold text-indigo-600 hover:text-indigo-900"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Section 1: Enrolment Confirmations */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
          <div className="flex justify-between items-center">
            <div>
              <h2 className="text-lg font-bold text-slate-900">My Academic Enrolments (FR-18, FR-19)</h2>
              <p className="text-xs text-slate-500">
                Confirm your active college admission to achieve Trust Tier T3 (Student Confirmed).
              </p>
            </div>
            <button
              type="button"
              onClick={loadAll}
              className="text-xs text-indigo-600 hover:text-indigo-800 font-semibold"
            >
              Refresh
            </button>
          </div>

          {enrolments.length === 0 && !loading && (
            <div className="p-8 text-center text-sm text-slate-500">
              No active academic enrolments reported for your APAAR ID.
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {enrolments.map((enr) => (
              <div
                key={enr.record_id}
                className="p-5 rounded-lg border border-slate-200 bg-slate-50 space-y-3"
              >
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="font-bold text-slate-900">{enr.institution_name}</h3>
                    <div className="text-xs font-mono text-slate-500">{enr.institution_code}</div>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-xs font-bold border ${tierBadge(
                      enr.trust_tier
                    )}`}
                  >
                    Tier T{enr.trust_tier}
                  </span>
                </div>

                <div className="text-xs text-slate-700 space-y-1">
                  <div>
                    <span className="text-slate-500">Programme:</span>{" "}
                    <span className="font-semibold">{enr.programme_name} ({enr.programme_code})</span>
                  </div>
                  <div>
                    <span className="text-slate-500">Academic Year:</span> {enr.academic_year}
                  </div>
                  {enr.admission_date && (
                    <div>
                      <span className="text-slate-500">Admission Date:</span> {enr.admission_date}
                    </div>
                  )}
                  <div>
                    <span className="text-slate-500">Confirmation Status:</span>{" "}
                    <span
                      className={`font-bold ${
                        enr.confirmation_status === "CONFIRMED"
                          ? "text-emerald-600"
                          : enr.confirmation_status === "DISPUTED"
                          ? "text-rose-600"
                          : "text-amber-600"
                      }`}
                    >
                      {enr.confirmation_status}
                    </span>
                  </div>
                </div>

                {/* Action buttons */}
                <div className="pt-2 flex flex-wrap items-center gap-2">
                  {enr.confirmation_status === "PENDING" && (
                    <>
                      <button
                        type="button"
                        onClick={() => handleConfirm(enr.confirmation_id)}
                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-md shadow-sm transition"
                      >
                        Confirm Enrolment (&rarr; T3)
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setDisputeId(enr.confirmation_id);
                          setDisputeReason("");
                        }}
                        className="px-3 py-1.5 border border-rose-300 text-rose-700 hover:bg-rose-50 text-xs font-semibold rounded-md transition"
                      >
                        Not Me (Dispute)
                      </button>
                    </>
                  )}
                  <Link
                    to={`/certificates/${enr.record_id}`}
                    className="px-3 py-1.5 border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 text-xs font-semibold rounded-md shadow-sm transition ml-auto"
                  >
                    View Degree Certificate &rarr;
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Dispute Modal */}
        {disputeId && (
          <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4">
              <h3 className="text-lg font-bold text-slate-900">Dispute Enrolment</h3>
              <p className="text-xs text-slate-500">
                If you were never admitted to this institution or programme, submitting a dispute will flag this record and open a formal AICTE review investigation.
              </p>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">
                  Reason for Dispute
                </label>
                <textarea
                  className="w-full text-xs border border-slate-300 rounded-md p-2.5 focus:ring-2 focus:ring-rose-500"
                  rows={3}
                  placeholder="e.g., I never applied or attended this college; my identity was used without consent."
                  value={disputeReason}
                  onChange={(e) => setDisputeReason(e.target.value)}
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setDisputeId(null)}
                  disabled={disputing}
                  className="px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100 rounded-md"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleDisputeSubmit}
                  disabled={disputing || !disputeReason.trim()}
                  className="px-4 py-1.5 bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold rounded-md shadow-sm transition disabled:opacity-50"
                >
                  {disputing ? "Submitting..." : "Submit Formal Dispute"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Section 2: Consent Requests & Selective Disclosure */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
          <div>
            <h2 className="text-lg font-bold text-slate-900">
              Verification Consent Requests (FR-27, FR-29)
            </h2>
            <p className="text-xs text-slate-500">
              Control exactly which fields external verifiers (employers, agencies) can inspect. Without your consent, zero data is revealed.
            </p>
          </div>

          {consentRequests.length === 0 && !loading && (
            <div className="p-6 text-center text-sm text-slate-500">
              No pending or active verification consent requests.
            </div>
          )}

          <div className="space-y-4">
            {consentRequests.map((req) => (
              <div
                key={req.id}
                className="p-5 rounded-lg border border-slate-200 bg-slate-50 space-y-3"
              >
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
                  <div>
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                      Request #{req.id} from {req.verifier_name} ({req.verifier_email})
                    </span>
                    <div className="text-sm font-bold text-slate-900 mt-0.5">
                      Purpose: &ldquo;{req.purpose}&rdquo;
                    </div>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-xs font-bold self-start md:self-auto ${
                      req.status === "GRANTED"
                        ? "bg-emerald-100 text-emerald-800"
                        : req.status === "DENIED"
                        ? "bg-rose-100 text-rose-800"
                        : req.status === "REVOKED"
                        ? "bg-slate-200 text-slate-700"
                        : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {req.status}
                  </span>
                </div>

                {/* Selective Disclosure Checkboxes when PENDING */}
                {req.status === "PENDING" && (
                  <div className="pt-2 border-t border-slate-200 space-y-2">
                    <div className="text-xs font-semibold text-slate-700">
                      Selective Disclosure — Choose fields to share:
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                      {req.requested_fields.map((f) => (
                        <label
                          key={f}
                          className="flex items-center gap-2 p-2 rounded bg-white border border-slate-200 text-xs text-slate-700 cursor-pointer hover:bg-indigo-50/50"
                        >
                          <input
                            type="checkbox"
                            checked={(selectedFields[req.id] || []).includes(f)}
                            onChange={() => handleToggleField(req.id, f)}
                            className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                          />
                          <span>{FIELD_LABELS[f] || f}</span>
                        </label>
                      ))}
                    </div>

                    <div className="pt-3 flex gap-2">
                      <button
                        type="button"
                        onClick={() => handleGrant(req.id)}
                        className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-md shadow-sm transition"
                      >
                        Grant Selected Fields
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeny(req.id)}
                        className="px-4 py-1.5 border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 text-xs font-semibold rounded-md shadow-sm transition"
                      >
                        Deny Access
                      </button>
                    </div>
                  </div>
                )}

                {/* Granted summary + Revoke button */}
                {req.status === "GRANTED" && (
                  <div className="pt-2 border-t border-slate-200 flex flex-col md:flex-row md:items-center justify-between gap-2 text-xs">
                    <div className="text-slate-600">
                      <span className="font-semibold text-slate-800">Granted fields:</span>{" "}
                      {(req.granted_fields || []).map((f) => FIELD_LABELS[f] || f).join(", ")}
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRevoke(req.id)}
                      className="px-3 py-1 bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 rounded text-xs font-semibold transition self-start md:self-auto"
                    >
                      Revoke Consent
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Section 3: Student Verification Access Log (FR-32) */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
          <div>
            <h2 className="text-lg font-bold text-slate-900">
              Verification Access Log (FR-32)
            </h2>
            <p className="text-xs text-slate-500">
              Audit record of all organizations that requested, inspected, or verified your academic credentials.
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                  <th className="py-2.5 px-3 font-semibold">Timestamp</th>
                  <th className="py-2.5 px-3 font-semibold">Verifier</th>
                  <th className="py-2.5 px-3 font-semibold">Purpose</th>
                  <th className="py-2.5 px-3 font-semibold">Fields Shared</th>
                  <th className="py-2.5 px-3 font-semibold">Outcome</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {accessLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-50 transition">
                    <td className="py-2.5 px-3 text-slate-500 whitespace-nowrap">
                      {new Date(log.timestamp).toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 font-medium text-slate-900">
                      {log.verifier_name}
                      <span className="text-[11px] text-slate-400 block">{log.verifier_email}</span>
                    </td>
                    <td className="py-2.5 px-3 text-slate-600">{log.purpose}</td>
                    <td className="py-2.5 px-3 text-slate-600">
                      {log.fields_shared.length > 0 ? (
                        log.fields_shared.map((f) => FIELD_LABELS[f] || f).join(", ")
                      ) : (
                        <span className="text-slate-400">None</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                          log.outcome === "GRANTED" || log.outcome === "VERIFIED"
                            ? "bg-emerald-100 text-emerald-800"
                            : log.outcome === "REVOKED"
                            ? "bg-purple-100 text-purple-800"
                            : "bg-rose-100 text-rose-800"
                        }`}
                      >
                        {log.outcome}
                      </span>
                    </td>
                  </tr>
                ))}
                {accessLogs.length === 0 && !loading && (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-slate-400">
                      No verification access events recorded yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
