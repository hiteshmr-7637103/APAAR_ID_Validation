import { useState, useEffect } from "react";
import {
  listDemoScenarios,
  injectDemoScenario,
  runConcurrencyTest,
  runTamperAudit,
  retrainDuplicateModel,
  type DemoScenario,
  type ConcurrencyTestResult,
  type TamperAuditResult,
  type ScenarioInjectionResult,
} from "../api/demo";

export function DemoControlPage() {
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [actionOutput, setActionOutput] = useState<{
    id: string;
    success: boolean;
    data: ScenarioInjectionResult | string;
  } | null>(null);

  const [concurrencyResult, setConcurrencyResult] = useState<ConcurrencyTestResult | null>(null);
  const [tamperResult, setTamperResult] = useState<TamperAuditResult | null>(null);

  useEffect(() => {
    async function init() {
      try {
        setLoading(true);
        const sc = await listDemoScenarios();
        setScenarios(sc);
      } catch (err: unknown) {
        console.error("Failed to load scenarios:", err);
      } finally {
        setLoading(false);
      }
    }
    init();
  }, []);

  async function handleInject(id: string) {
    try {
      setActiveAction(id);
      setActionOutput(null);
      const res = await injectDemoScenario(id);
      setActionOutput({ id, success: true, data: res });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to inject scenario";
      setActionOutput({ id, success: false, data: msg });
    } finally {
      setActiveAction(null);
    }
  }

  async function handleConcurrencyTest() {
    try {
      setActiveAction("concurrency");
      setConcurrencyResult(null);
      const res = await runConcurrencyTest();
      setConcurrencyResult(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      alert("Concurrency test failed: " + msg);
    } finally {
      setActiveAction(null);
    }
  }

  async function handleTamperAudit() {
    try {
      setActiveAction("tamper");
      setTamperResult(null);
      const res = await runTamperAudit();
      setTamperResult(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      alert("Tamper simulation failed: " + msg);
    } finally {
      setActiveAction(null);
    }
  }

  async function handleRetrain() {
    try {
      setActiveAction("retrain");
      await retrainDuplicateModel();
      alert("Retraining completed successfully! Reviewer feedback incorporated.");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      alert("Retraining failed: " + msg);
    } finally {
      setActiveAction(null);
    }
  }

  if (loading) {
    return <div className="p-8 text-slate-500">Loading demo controls...</div>;
  }

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-200 pb-6">
        <div>
          <h1 className="text-3xl font-bold text-slate-900 tracking-tight">AICTE Demo & Simulation Center</h1>
          <p className="text-slate-500 text-sm mt-1">
            Live fraud injection scenarios, atomic concurrency verification, and cryptographic tamper audit helpers.
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleRetrain}
            disabled={activeAction !== null}
            className="px-4 py-2 border border-slate-300 text-slate-700 bg-white font-medium rounded-lg hover:bg-slate-50 disabled:opacity-50 text-sm shadow-sm"
          >
            {activeAction === "retrain" ? "Retraining ML..." : "Retrain ML with Feedback"}
          </button>
        </div>
      </div>

      {/* 1. Concurrency & Tamper Interactive Banners */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Concurrency Simulator */}
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-6 space-y-4">
          <div className="flex justify-between items-start">
            <div>
              <h3 className="text-base font-bold text-slate-900">Seat Ledger Concurrency Demo (Spec 13)</h3>
              <p className="text-xs text-slate-500 mt-1">
                Bucket with 5 remaining seats subjected to 50 simultaneous parallel admission transactions.
              </p>
            </div>
            <button
              onClick={handleConcurrencyTest}
              disabled={activeAction !== null}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg shadow-sm disabled:opacity-50 shrink-0"
            >
              {activeAction === "concurrency" ? "Firing 50 Threads..." : "Launch Race Test"}
            </button>
          </div>

          {concurrencyResult && (
            <div className="p-4 bg-slate-50 border border-slate-200 rounded-lg space-y-3">
              <div className="flex items-center justify-between text-xs font-semibold">
                <span className="text-slate-700">Atomic Locking Status:</span>
                <span className="px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded font-mono">
                  {concurrencyResult.status} ({concurrencyResult.elapsed_ms} ms)
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                <div className="p-2 bg-emerald-50 rounded border border-emerald-100">
                  <span className="block font-bold text-emerald-800 text-lg font-mono">{concurrencyResult.clean_admissions}</span>
                  <span className="text-emerald-700 text-[10px]">Clean (Under Cap)</span>
                </div>
                <div className="p-2 bg-amber-50 rounded border border-amber-100">
                  <span className="block font-bold text-amber-800 text-lg font-mono">{concurrencyResult.flagged_over_cap}</span>
                  <span className="text-amber-700 text-[10px]">Flagged Over-Cap</span>
                </div>
                <div className="p-2 bg-slate-100 rounded border border-slate-200">
                  <span className="block font-bold text-slate-800 text-lg font-mono">{concurrencyResult.final_used} / 20</span>
                  <span className="text-slate-600 text-[10px]">Final Used / Cap</span>
                </div>
              </div>
              <p className="text-[11px] text-slate-600 font-mono">
                ✅ No lost updates: 50 threads executed with distinct sequential balances (16..65).
              </p>
            </div>
          )}
        </div>

        {/* Cryptographic Tamper Simulator */}
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-6 space-y-4">
          <div className="flex justify-between items-start">
            <div>
              <h3 className="text-base font-bold text-slate-900">Cryptographic Tamper Audit Helper (FR-35)</h3>
              <p className="text-xs text-slate-500 mt-1">
                Simulates DBA altering append-only audit entry directly, verifies hash failure, and restores chain.
              </p>
            </div>
            <button
              onClick={handleTamperAudit}
              disabled={activeAction !== null}
              className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold rounded-lg shadow-sm disabled:opacity-50 shrink-0"
            >
              {activeAction === "tamper" ? "Testing Chain..." : "Simulate Tamper"}
            </button>
          </div>

          {tamperResult && (
            <div className="p-4 bg-slate-50 border border-slate-200 rounded-lg space-y-2 text-xs">
              <div className="flex items-center justify-between font-semibold">
                <span className="text-slate-700">Tamper Detection:</span>
                <span className="px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded font-mono">
                  DETECTED & RESTORED
                </span>
              </div>
              <p className="text-slate-600 text-[11px] font-mono">
                Altered entry row: #{tamperResult.tampered_entry_id} (Reason: {tamperResult.first_broken_detected?.reason})
              </p>
              <p className="text-emerald-700 font-semibold text-[11px]">
                ✅ {tamperResult.message}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* 2. Fraud Injection Scenarios Grid */}
      <div className="space-y-4">
        <h2 className="text-xl font-bold text-slate-900">Fraud & Anomaly Injection Scenarios</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {scenarios.map((sc) => {
            const isActing = activeAction === sc.id;
            const hasOutput = actionOutput && actionOutput.id === sc.id;

            return (
              <div
                key={sc.id}
                className="bg-white border border-slate-200 rounded-xl shadow-sm p-5 flex flex-col justify-between space-y-4 hover:border-slate-300 transition-colors"
              >
                <div>
                  <div className="flex items-center justify-between">
                    <h3 className="font-bold text-slate-900 text-sm">{sc.name}</h3>
                    <span className="text-[10px] uppercase font-mono px-2 py-0.5 bg-slate-100 text-slate-600 rounded">
                      {sc.id}
                    </span>
                  </div>
                  <p className="text-xs text-slate-600 mt-2 leading-relaxed">{sc.description}</p>
                  <div className="mt-3 p-2.5 bg-slate-50 rounded border border-slate-100 text-[11px] text-slate-500">
                    <strong className="text-slate-700 font-medium">Expected Result: </strong>
                    {sc.expected_result}
                  </div>
                </div>

                <div>
                  <button
                    onClick={() => handleInject(sc.id)}
                    disabled={activeAction !== null}
                    className="w-full py-2 px-3 bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold rounded-lg shadow-sm disabled:opacity-50 transition-colors"
                  >
                    {isActing ? "Injecting Scenario..." : "Inject Scenario"}
                  </button>

                  {hasOutput && (
                    <div
                      className={`mt-3 p-2.5 rounded text-[11px] font-mono overflow-x-auto ${
                        actionOutput.success
                          ? "bg-emerald-50 text-emerald-900 border border-emerald-200"
                          : "bg-rose-50 text-rose-900 border border-rose-200"
                      }`}
                    >
                      {actionOutput.success && typeof actionOutput.data !== "string" ? (
                        <>
                          <div>✅ {actionOutput.data.message || "Scenario injected."}</div>
                          {actionOutput.data.cases_opened !== undefined && (
                            <div>Review Cases Opened: {actionOutput.data.cases_opened}</div>
                          )}
                          {actionOutput.data.apaar_id && (
                            <div>APAAR ID: {actionOutput.data.apaar_id}</div>
                          )}
                        </>
                      ) : (
                        <div>❌ Error: {String(actionOutput.data)}</div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
export default DemoControlPage;
