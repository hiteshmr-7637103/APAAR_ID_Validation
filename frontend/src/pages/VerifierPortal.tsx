import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../components/AppShell";
import {
  fetchConsentRequests,
  submitVerificationRequest,
  fetchVerifiedRecord,
  type ConsentRequestItem,
  type VerifiedCredential,
} from "../api/verification";

const AVAILABLE_FIELDS = [
  { key: "student_name", label: "Student Name" },
  { key: "dob", label: "Date of Birth" },
  { key: "gender", label: "Gender" },
  { key: "institution_name", label: "Institution Name" },
  { key: "institution_code", label: "Institution Code" },
  { key: "programme_name", label: "Programme Name" },
  { key: "programme_code", label: "Programme Code" },
  { key: "academic_year", label: "Academic Year" },
  { key: "admission_date", label: "Admission Date" },
  { key: "student_college_id", label: "College Roll / Registration No." },
  { key: "status", label: "Enrolment Status" },
  { key: "trust_tier", label: "Trust Tier" },
];

export function VerifierPortalPage() {
  const [requests, setRequests] = useState<ConsentRequestItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // New Request Form State
  const [apaarId, setApaarId] = useState("");
  const [purpose, setPurpose] = useState("Pre-employment academic credential verification");
  const [selectedFields, setSelectedFields] = useState<string[]>([
    "student_name",
    "institution_name",
    "programme_name",
    "status",
    "trust_tier",
  ]);
  const [submitting, setSubmitting] = useState(false);

  // Inspector modal state
  const [activeConsentId, setActiveConsentId] = useState<number | null>(null);
  const [credential, setCredential] = useState<VerifiedCredential | null>(null);
  const [loadingCred, setLoadingCred] = useState(false);

  const loadRequests = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchConsentRequests();
      setRequests(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Error loading requests: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  const handleSubmitRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apaarId.trim() || !purpose.trim() || selectedFields.length === 0) {
      alert("Please provide APAAR ID, purpose, and at least one requested field.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await submitVerificationRequest(apaarId.trim(), purpose.trim(), selectedFields);
      setActionMessage(`Verification request #${res.consent_id} submitted successfully.`);
      setApaarId("");
      loadRequests();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Request submission failed: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleInspectCredential = async (consentId: number) => {
    setActiveConsentId(consentId);
    setLoadingCred(true);
    setCredential(null);
    try {
      const data = await fetchVerifiedRecord(consentId);
      setCredential(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Failed to fetch credential: ${msg}`);
      setActiveConsentId(null);
    } finally {
      setLoadingCred(false);
    }
  };

  const toggleField = (field: string) => {
    if (selectedFields.includes(field)) {
      setSelectedFields(selectedFields.filter((f) => f !== field));
    } else {
      setSelectedFields([...selectedFields, field]);
    }
  };

  return (
    <AppShell area="verifier">
      <div className="space-y-6">
        {/* Header */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
          <h1 className="text-2xl font-bold text-slate-900">Authorized Verifier Portal</h1>
          <p className="text-sm text-slate-500 mt-1">
            Request student academic verification under the Indian Digital Personal Data Protection (DPDP) consent framework.
          </p>
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

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* New Request Form */}
          <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
            <div>
              <h2 className="text-lg font-bold text-slate-900">New Verification Request (FR-26)</h2>
              <p className="text-xs text-slate-500">
                Submit an inquiry keyed on APAAR ID with purpose and required attributes.
              </p>
            </div>

            <form onSubmit={handleSubmitRequest} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Student 12-digit APAAR ID
                </label>
                <input
                  type="text"
                  maxLength={12}
                  placeholder="e.g. 990274831650"
                  className="w-full text-sm font-mono border border-slate-300 rounded-md p-2 focus:ring-2 focus:ring-indigo-500"
                  value={apaarId}
                  onChange={(e) => setApaarId(e.target.value.replace(/\D/g, ""))}
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Verification Purpose
                </label>
                <textarea
                  rows={2}
                  className="w-full text-xs border border-slate-300 rounded-md p-2 focus:ring-2 focus:ring-indigo-500"
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Requested Fields
                </label>
                <div className="grid grid-cols-1 gap-1.5 max-h-48 overflow-y-auto p-1 border border-slate-100 rounded">
                  {AVAILABLE_FIELDS.map((f) => (
                    <label
                      key={f.key}
                      className="flex items-center gap-2 p-1.5 rounded bg-slate-50 text-xs text-slate-700 cursor-pointer hover:bg-indigo-50/50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedFields.includes(f.key)}
                        onChange={() => toggleField(f.key)}
                        className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <span>{f.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="w-full py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold rounded-md shadow-sm transition disabled:opacity-50"
              >
                {submitting ? "Submitting Request..." : "Send Verification Request"}
              </button>
            </form>
          </div>

          {/* Requests List */}
          <div className="lg:col-span-2 bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
            <div className="flex justify-between items-center">
              <div>
                <h2 className="text-lg font-bold text-slate-900">Verification Requests Tracker</h2>
                <p className="text-xs text-slate-500">
                  Status of your verification inquiries. Data becomes inspectable once the student grants consent.
                </p>
              </div>
              <button
                type="button"
                onClick={loadRequests}
                className="text-xs text-indigo-600 hover:text-indigo-800 font-semibold"
              >
                Refresh
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                    <th className="py-2.5 px-3 font-semibold">ID</th>
                    <th className="py-2.5 px-3 font-semibold">APAAR ID</th>
                    <th className="py-2.5 px-3 font-semibold">Purpose</th>
                    <th className="py-2.5 px-3 font-semibold">Status</th>
                    <th className="py-2.5 px-3 font-semibold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {requests.map((r) => (
                    <tr key={r.id} className="hover:bg-slate-50 transition">
                      <td className="py-2.5 px-3 font-mono text-slate-500">#{r.id}</td>
                      <td className="py-2.5 px-3 font-mono font-medium text-slate-900">
                        {r.student_apaar_id || "-"}
                      </td>
                      <td className="py-2.5 px-3 text-slate-600 max-w-xs truncate" title={r.purpose}>
                        {r.purpose}
                      </td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                            r.status === "GRANTED"
                              ? "bg-emerald-100 text-emerald-800"
                              : r.status === "DENIED"
                              ? "bg-rose-100 text-rose-800"
                              : r.status === "REVOKED"
                              ? "bg-purple-100 text-purple-800"
                              : "bg-amber-100 text-amber-800"
                          }`}
                        >
                          {r.status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        {r.status === "GRANTED" ? (
                          <button
                            type="button"
                            onClick={() => handleInspectCredential(r.id)}
                            className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 text-white rounded font-semibold text-[11px] shadow-sm transition"
                          >
                            Inspect Credential &rarr;
                          </button>
                        ) : (
                          <span className="text-slate-400 text-[11px]">No access</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {requests.length === 0 && !loading && (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-slate-400">
                        No verification requests submitted yet. Use the form on the left.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Credential Inspector Modal */}
        {activeConsentId && (
          <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto">
              <div className="flex justify-between items-start border-b border-slate-200 pb-3">
                <div>
                  <h3 className="text-lg font-bold text-slate-900">
                    Verified Digital Credential (FR-28, FR-29)
                  </h3>
                  <p className="text-xs text-slate-500">
                    Cryptographically signed by AICTE Ed25519 Authority Key.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveConsentId(null)}
                  className="text-slate-400 hover:text-slate-600 text-lg font-bold"
                >
                  &times;
                </button>
              </div>

              {loadingCred && (
                <div className="p-8 text-center text-sm text-indigo-600">
                  Retrieving cryptographically verified record...
                </div>
              )}

              {credential && (
                <div className="space-y-4">
                  {/* Security Proof Badge */}
                  <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg flex items-center justify-between text-xs text-emerald-800">
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                      <span className="font-semibold">Ed25519 Signature Verified</span>
                    </div>
                    <span className="font-mono text-[11px] text-emerald-700">
                      Key: {credential.credential.key_id}
                    </span>
                  </div>

                  {/* Disclosed Fields Table */}
                  <div className="border border-slate-200 rounded-lg overflow-hidden">
                    <div className="p-2.5 bg-slate-50 font-bold text-xs text-slate-700 border-b border-slate-200">
                      Disclosed Student Attributes (Student-Authorized)
                    </div>
                    <div className="divide-y divide-slate-100 text-xs">
                      {Object.entries(credential.credential.disclosed_fields).map(([k, v]) => (
                        <div key={k} className="p-2.5 flex justify-between">
                          <span className="font-medium text-slate-600 uppercase tracking-wider text-[11px]">
                            {k.replace(/_/g, " ")}
                          </span>
                          <span className="font-semibold text-slate-900">
                            {v !== null && v !== undefined ? String(v) : "-"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* QR Code and Compact Payload */}
                  <div className="p-4 bg-slate-50 border border-slate-200 rounded-lg flex flex-col md:flex-row items-center gap-4">
                    <img
                      src={`/api/v1/verify/qr/${activeConsentId}.png`}
                      alt="Verification QR Code"
                      className="w-32 h-32 border border-slate-300 rounded bg-white p-1"
                    />
                    <div className="space-y-2 text-xs flex-1">
                      <div className="font-bold text-slate-900">Offline-Verifiable QR Code (FR-30)</div>
                      <p className="text-slate-500 text-[11px]">
                        Scan this QR with any offline scanner or the public verifier at <code>/verify</code>.
                        Verifies signature without contacting the server.
                      </p>
                      <div className="font-mono text-[10px] bg-white p-2 rounded border border-slate-200 break-all max-h-16 overflow-y-auto">
                        {credential.qr_string}
                      </div>
                    </div>
                  </div>

                  <div className="flex justify-end pt-2">
                    <button
                      type="button"
                      onClick={() => setActiveConsentId(null)}
                      className="px-4 py-2 bg-slate-800 text-white text-xs font-semibold rounded-md shadow-sm"
                    >
                      Close Inspector
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
