import { apiFetch } from "./client";

export interface EvaluationMetrics {
  evaluated_at: string;
  evaluation_duration_sec: number;
  disclaimer: string;
  duplicate_matcher: {
    in_distribution: {
      precision: number;
      recall: number;
      f1: number;
      pr_auc: number;
      brier_score: number;
      calibration_error: number;
      sample_count: number;
    };
    held_out: {
      precision: number;
      recall: number;
      f1: number;
      pr_auc: number;
      brier_score: number;
      calibration_error: number;
      sample_count: number;
    };
    model_architecture: string;
    features_count: number;
  };
  rule_engine: {
    fault_detection_rate_pct: number;
    scenarios_tested: number;
    scenarios_detected: number;
    faults: Record<
      string,
      {
        detected: boolean;
        detected_codes: string[];
        status: string;
      }
    >;
  };
  anomaly_and_rings: {
    anomaly_detector: {
      model: string;
      explanation_method: string;
      top_10_percent_ranking_rate_pct: number;
      target_floor_pct: number;
      anomalies_detected_count: number;
      status: string;
    };
    ring_detector: {
      algorithm: string;
      ring_detection_recall_pct: number;
      rings_detected_count: number;
      status: string;
    };
  };
  seat_ledger_concurrency: {
    scenario: string;
    parallel_racing_threads: number;
    remaining_seats_initial: number;
    clean_admissions_count: number;
    flagged_over_cap_count: number;
    lost_updates_count: number;
    excess_seats_reported: number;
    atomicity_mechanism: string;
    concurrency_status: string;
  };
  performance: {
    single_record_validation_p50_ms: number;
    single_record_validation_p95_ms: number;
    projected_throughput_rows_per_sec: number;
    projected_10k_dry_run_time_sec: number;
    benchmark_sample_size: number;
  };
  review_time_saved: {
    total_records_analyzed: number;
    clean_records_automated: number;
    flagged_records_requiring_human_review: number;
    automated_clearance_rate_pct: number;
    stated_assumption: string;
    estimated_human_audit_hours_saved: number;
  };
}

export async function getEvaluation(): Promise<EvaluationMetrics> {
  return apiFetch<EvaluationMetrics>("/evaluation");
}

export async function runEvaluation(): Promise<EvaluationMetrics> {
  return apiFetch<EvaluationMetrics>("/evaluation/run", {
    method: "POST",
  });
}
