import type { BucketImpact } from "../api/uploads";
import { t } from "../i18n";

/** Projected (dry run) or recorded seat-bucket impact of an upload. */
export function BucketPanel({ impact }: { impact: BucketImpact[] }) {
  if (!impact.length) return <p className="text-sm text-slate-500">{t("upload.noBuckets")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-slate-500">
          <tr>
            <th className="py-1 pr-3">Programme</th>
            <th className="py-1 pr-3">Bucket</th>
            <th className="py-1 pr-3 text-right">Cap</th>
            <th className="py-1 pr-3 text-right">Used now</th>
            <th className="py-1 pr-3 text-right">Rows in file</th>
            <th className="py-1 pr-3 text-right">Used after</th>
            <th className="py-1 pr-3 text-right">Excess</th>
          </tr>
        </thead>
        <tbody>
          {impact.map((b) => (
            <tr
              key={`${b.institution_programme_id}-${b.ledger_key}`}
              className={`border-t border-slate-100 ${b.would_be_flagged ? "bg-amber-50 text-amber-900" : ""}`}
            >
              <td className="py-1 pr-3">
                {b.programme_code} <span className="text-xs text-slate-500">({b.shift})</span>
              </td>
              <td className="py-1 pr-3 font-mono text-xs">{b.ledger_key}</td>
              <td className="py-1 pr-3 text-right">{b.cap}</td>
              <td className="py-1 pr-3 text-right">{b.used_before}</td>
              <td className="py-1 pr-3 text-right">{b.rows_in_file}</td>
              <td className="py-1 pr-3 text-right">{b.used_after}</td>
              <td className="py-1 pr-3 text-right font-medium">{b.excess_after || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="mt-3 space-y-1">
        {impact
          .filter((b) => b.would_be_flagged)
          .map((b) => (
            <li
              key={`m-${b.institution_programme_id}-${b.ledger_key}`}
              className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
            >
              {b.message_en}
            </li>
          ))}
      </ul>
    </div>
  );
}
