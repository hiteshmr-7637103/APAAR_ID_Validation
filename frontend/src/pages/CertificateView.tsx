import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { fetchCertificate, type CertificateData } from "../api/verification";

export function CertificateViewPage() {
  const { recordId } = useParams<{ recordId: string }>();
  const [data, setData] = useState<CertificateData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!recordId) return;
    fetchCertificate(Number(recordId))
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Failed to load certificate: ${msg}`);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [recordId]);

  return (
    <div className="min-h-screen bg-slate-100 py-8 px-4 flex flex-col items-center print:bg-white print:p-0">
      {/* Print & Back Controls (hidden on print) */}
      <div className="max-w-3xl w-full mb-4 flex justify-between items-center print:hidden">
        <Link
          to="/student"
          className="text-xs font-semibold text-slate-600 hover:text-slate-900 flex items-center gap-1"
        >
          &larr; Back to Student Portal
        </Link>
        <button
          type="button"
          onClick={() => window.print()}
          disabled={!data}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold rounded-md shadow-sm transition flex items-center gap-2"
        >
          <span>🖶</span>
          <span>Print Certificate</span>
        </button>
      </div>

      {error && (
        <div className="max-w-md w-full bg-rose-50 border border-rose-300 text-rose-800 p-4 rounded-md text-sm text-center">
          {error}
        </div>
      )}

      {loading && (
        <div className="p-12 text-sm text-slate-500">Generating certificate document...</div>
      )}

      {/* Official Certificate Paper Container */}
      {data && (
        <div className="max-w-3xl w-full bg-white border-8 border-double border-slate-700 p-10 md:p-14 rounded-sm shadow-xl print:shadow-none print:border-4 print:p-8 space-y-8 relative overflow-hidden">
          {/* Subtle watermark */}
          <div className="absolute inset-0 flex items-center justify-center opacity-[0.03] pointer-events-none select-none text-9xl font-black rotate-[-25deg]">
            AICTE VERIFIED
          </div>

          {/* Certificate Header */}
          <div className="text-center space-y-2 border-b-2 border-slate-300 pb-6">
            <div className="text-xs font-bold tracking-widest uppercase text-slate-500">
              ALL INDIA COUNCIL FOR TECHNICAL EDUCATION
            </div>
            <h1 className="text-2xl md:text-3xl font-serif font-black text-slate-900 tracking-wide uppercase">
              {data.institution_name}
            </h1>
            <div className="text-xs font-mono text-slate-600 tracking-wider">
              INSTITUTION CODE: {data.institution_code}
            </div>
          </div>

          {/* Main Award Title */}
          <div className="text-center space-y-1">
            <div className="text-sm font-serif italic text-slate-600">
              This official transcript and credential validates that
            </div>
            <div className="text-2xl md:text-3xl font-serif font-bold text-slate-900 underline decoration-slate-300 underline-offset-8">
              {data.student_name}
            </div>
          </div>

          {/* PPT Contract Claim P11 / FR-34: APAAR ID right next to Student ID */}
          <div className="max-w-lg mx-auto bg-slate-50 border border-slate-300 rounded p-4 text-center">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
              National Unique Academic Identity
            </div>
            <div className="flex flex-col sm:flex-row justify-center items-center gap-4 sm:gap-8 text-sm">
              <div>
                <span className="text-slate-500 text-xs block">Student ID / Roll No.</span>
                <span className="font-mono font-bold text-slate-900">
                  {data.student_college_id || "NOT-ASSIGNED"}
                </span>
              </div>
              <div className="hidden sm:block text-slate-300">|</div>
              <div>
                <span className="text-slate-500 text-xs block">APAAR ID</span>
                <span className="font-mono font-extrabold text-indigo-700 tracking-wider">
                  {data.apaar_id}
                </span>
              </div>
            </div>
          </div>

          {/* Body Text */}
          <div className="text-center text-sm md:text-base font-serif text-slate-700 max-w-xl mx-auto leading-relaxed">
            has been formally admitted and verified in the programme of study{" "}
            <span className="font-bold text-slate-900">{data.programme_name}</span> (Code:{" "}
            <span className="font-mono text-xs">{data.programme_code}</span>) commencing in academic year{" "}
            <span className="font-semibold text-slate-900">{data.admission_date.slice(0, 4)}</span>.
          </div>

          {/* Footer Seals & Verification QR (FR-34) */}
          <div className="pt-6 border-t-2 border-slate-200 flex flex-col sm:flex-row justify-between items-center gap-6">
            {/* Verification QR Code */}
            <div className="flex items-center gap-3">
              <div className="p-1 bg-white border border-slate-300 rounded shadow-sm">
                <img
                  src={`https://api.qrserver.com/v1/create-qr-code/?size=100x100&data=${encodeURIComponent(
                    data.qr_string
                  )}`}
                  alt="Certificate Verification QR"
                  className="w-20 h-20"
                />
              </div>
              <div className="text-left text-[10px] text-slate-500 space-y-0.5">
                <div className="font-bold text-slate-800">Scan to Verify Offline</div>
                <div>Ed25519 Digitally Signed</div>
                <div className="font-mono text-[9px] text-slate-400">Key: {data.key_id}</div>
              </div>
            </div>

            {/* Trust Tier Badge Seal */}
            <div className="text-center p-3 rounded-full border-2 border-dashed border-emerald-600 bg-emerald-50 w-24 h-24 flex flex-col justify-center items-center">
              <div className="text-[9px] uppercase font-bold text-emerald-800 tracking-widest">
                VERIFIED
              </div>
              <div className="text-lg font-black text-emerald-700">T{data.trust_tier}</div>
              <div className="text-[8px] text-emerald-600">CONFIRMED</div>
            </div>

            {/* Date & Registrar Authority */}
            <div className="text-right text-xs text-slate-600 space-y-1">
              <div>Issue Date: {data.issued_at}</div>
              <div className="pt-4 font-serif font-bold text-slate-900">
                Registrar & Controller of Examinations
              </div>
              <div className="text-[10px] text-slate-400">AICTE National Academic Depository</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
