import { useState } from "react";
import { Link } from "react-router-dom";
import { verifyOffline, type OfflineVerifyResult } from "../api/verification";

export function PublicVerifyPage() {
  const [payloadText, setPayloadText] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [result, setResult] = useState<OfflineVerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleVerify = async (textToVerify?: string) => {
    const raw = textToVerify || payloadText;
    if (!raw.trim()) {
      alert("Please paste a verification QR payload or credential JSON.");
      return;
    }
    setVerifying(true);
    setResult(null);
    setError(null);
    try {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new Error("Invalid JSON format. Please paste a valid QR or credential payload.");
      }
      const data = await verifyOffline(parsed);
      setResult(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setVerifying(false);
    }
  };

  const handleLoadTampered = () => {
    try {
      const parsed = JSON.parse(payloadText);
      if (parsed.tier !== undefined) {
        // Tamper with the trust tier (e.g. raise to 3 fraudulently)
        parsed.tier = parsed.tier === 3 ? 1 : 3;
      } else if (parsed.disclosed_fields) {
        parsed.disclosed_fields.student_name = "Tampered Identity";
      } else {
        parsed.status = "FRAUDULENT";
      }
      const tamperedStr = JSON.stringify(parsed, null, 2);
      setPayloadText(tamperedStr);
      handleVerify(tamperedStr);
    } catch {
      alert("Please first generate or paste a payload to tamper with.");
    }
  };

  return (
    <div className="min-h-screen bg-slate-100 flex flex-col justify-between">
      {/* Top Navbar */}
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xl font-bold tracking-tight text-slate-900">
              APAAR<span className="text-indigo-600">Ledger</span>
            </span>
            <span className="text-xs px-2 py-0.5 rounded bg-indigo-100 text-indigo-800 font-bold uppercase tracking-wider">
              Offline Verifier
            </span>
          </div>
          <Link
            to="/login"
            className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
          >
            Sign In to Portal &rarr;
          </Link>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-3xl w-full mx-auto p-6 space-y-6">
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 text-center space-y-2">
          <h1 className="text-2xl font-extrabold text-slate-900">
            Public Academic Credential Verifier (FR-30)
          </h1>
          <p className="text-sm text-slate-500 max-w-xl mx-auto">
            Verify Ed25519 digitally signed APAAR educational credentials without trusting intermediate parties.
            Works completely offline once keys are cached.
          </p>
        </div>

        {/* Input Card */}
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
          <div>
            <label className="block text-xs font-bold uppercase text-slate-700 tracking-wider mb-1">
              Paste QR Code String or Credential JSON
            </label>
            <textarea
              rows={6}
              placeholder='e.g. {"kid":"key-abc","apaar":"1650","inst":"DEMO-INST-001","prog":"BTECH-CSE","status":"ACTIVE","tier":3,"iat":"2026-11-01","sig":"..."}'
              className="w-full text-xs font-mono border border-slate-300 rounded-md p-3 focus:ring-2 focus:ring-indigo-500"
              value={payloadText}
              onChange={(e) => setPayloadText(e.target.value)}
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => handleVerify()}
                disabled={verifying || !payloadText.trim()}
                className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold rounded-md shadow-sm transition disabled:opacity-50"
              >
                {verifying ? "Verifying Cryptography..." : "Verify Credential"}
              </button>
              {payloadText.trim() && (
                <button
                  type="button"
                  onClick={handleLoadTampered}
                  className="px-3 py-2 border border-rose-300 text-rose-700 hover:bg-rose-50 text-xs font-semibold rounded-md transition"
                >
                  Test Tamper Detection
                </button>
              )}
            </div>
            <button
              type="button"
              onClick={() => {
                setPayloadText("");
                setResult(null);
                setError(null);
              }}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              Clear
            </button>
          </div>
        </div>

        {error && (
          <div className="bg-rose-50 border border-rose-300 text-rose-800 p-4 rounded-md text-sm">
            {error}
          </div>
        )}

        {/* Verification Result Card */}
        {result && (
          <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200 space-y-4">
            <div
              className={`p-4 rounded-lg border text-center ${
                result.outcome === "VALID"
                  ? "bg-emerald-50 border-emerald-300 text-emerald-900"
                  : result.outcome === "EXPIRED"
                  ? "bg-amber-50 border-amber-300 text-amber-900"
                  : "bg-rose-50 border-rose-300 text-rose-900"
              }`}
            >
              <div className="text-xl font-black uppercase tracking-wider">
                {result.outcome === "VALID" && "✓ Valid & Cryptographically Proven"}
                {result.outcome === "INVALID" && "✗ Invalid / Tampered Signature"}
                {result.outcome === "EXPIRED" && "⚠ Credential Expired"}
                {result.outcome === "REVOKED" && "⊘ Credential Revoked by Student"}
              </div>
              <div className="text-xs mt-1 opacity-90">
                {result.outcome === "VALID"
                  ? "The cryptographic Ed25519 signature is authentic and verified with the AICTE public key."
                  : result.reason || "The credential cannot be verified."}
              </div>
            </div>

            {result.outcome === "VALID" && (
              <div className="border border-slate-200 rounded-lg overflow-hidden divide-y divide-slate-100 text-xs">
                {result.apaar_suffix && (
                  <div className="p-3 flex justify-between bg-slate-50">
                    <span className="text-slate-500 font-medium">APAAR ID Suffix</span>
                    <span className="font-mono font-bold text-slate-900">
                      ***-***-*{result.apaar_suffix}
                    </span>
                  </div>
                )}
                {result.institution_code && (
                  <div className="p-3 flex justify-between">
                    <span className="text-slate-500 font-medium">Institution Code</span>
                    <span className="font-semibold text-slate-900">{result.institution_code}</span>
                  </div>
                )}
                {result.programme_code && (
                  <div className="p-3 flex justify-between bg-slate-50">
                    <span className="text-slate-500 font-medium">Programme</span>
                    <span className="font-semibold text-slate-900">{result.programme_code}</span>
                  </div>
                )}
                {result.status && (
                  <div className="p-3 flex justify-between">
                    <span className="text-slate-500 font-medium">Enrolment Status</span>
                    <span className="font-bold text-emerald-600">{result.status}</span>
                  </div>
                )}
                {result.trust_tier !== undefined && (
                  <div className="p-3 flex justify-between bg-slate-50">
                    <span className="text-slate-500 font-medium">Trust Tier</span>
                    <span className="font-bold text-indigo-600">Tier T{result.trust_tier}</span>
                  </div>
                )}
                {result.key_id && (
                  <div className="p-3 flex justify-between">
                    <span className="text-slate-500 font-medium">Signing Key ID</span>
                    <span className="font-mono text-slate-600">{result.key_id}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="text-center py-6 text-xs text-slate-400">
        AICTE APAAR ID Validation Platform &bull; SIH 2026 Prototype
      </footer>
    </div>
  );
}
