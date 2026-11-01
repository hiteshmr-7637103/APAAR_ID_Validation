import { apiFetch, apiFetchRaw } from "./client";

export interface ReportMeta {
  name: string;
  title: string;
  description: string;
}

export interface ReportPreview {
  report: string;
  academic_year: string;
  state: string | null;
  headers: string[];
  rows: (string | number)[][];
  total_rows: number;
}

export function fetchReportsList() {
  return apiFetch<{ reports: ReportMeta[] }>("/reports");
}

export function fetchReportPreview(
  name: string,
  academicYear?: string,
  state?: string,
) {
  const params = new URLSearchParams({ format: "json" });
  if (academicYear) params.set("academic_year", academicYear);
  if (state) params.set("state", state);
  return apiFetch<ReportPreview>(`/reports/${name}?${params.toString()}`);
}

export async function downloadReportCsv(
  name: string,
  academicYear?: string,
  state?: string,
) {
  const params = new URLSearchParams({ format: "csv" });
  if (academicYear) params.set("academic_year", academicYear);
  if (state) params.set("state", state);

  const res = await apiFetchRaw(`/reports/${name}?${params.toString()}`);
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const disposition = res.headers.get("Content-Disposition");
  let filename = `${name}.csv`;
  if (disposition && disposition.includes("filename=")) {
    filename = disposition.split("filename=")[1].replace(/["']/g, "");
  }
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
