import { motion } from "motion/react"
import type { MonitoringReport } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

function Stat({ label, value, tone = "ink" }: { label: string; value: string | number; tone?: "ink" | "amber" | "emerald" }) {
  const cls =
    tone === "amber"
      ? "text-amber-700"
      : tone === "emerald"
        ? "text-emerald-700"
        : "text-[color:var(--color-ink)]"
  return (
    <div className="rounded-md border border-[color:var(--color-hair)] bg-[color:var(--color-card-2)] px-3 py-2.5">
      <div className="eyebrow">{label}</div>
      <div className={`mt-1 font-mono text-[22px] font-semibold tabular-nums ${cls}`}>{value}</div>
    </div>
  )
}

/** Post-visit monitoring report — SDV summary, protocol compliance, deviations,
 *  data queries, and a sign-off-ready badge with the recommendation. */
export function MonitoringReportPanel({ report }: { report: MonitoringReport }) {
  const sdv = report.sdv
  const compliance = report.protocol_compliance
  const deviations = report.deviations ?? []
  const queries = report.queries ?? []
  const ready = !!report.signoff_ready

  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease }}
      className="card mt-4 p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">Monitoring report</h2>
          <span
            className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
              ready
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-amber-300 bg-amber-50 text-amber-800"
            }`}
          >
            {ready ? "✓ sign-off ready" : "not sign-off ready"}
          </span>
        </div>
        <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
          {report.study} · {report.subject} · {report.visit}
        </span>
      </div>

      {report.summary && (
        <p className="mt-3 max-w-[80ch] text-[13px] leading-relaxed text-[color:var(--color-ink-body)]">
          {report.summary}
        </p>
      )}

      {sdv && (
        <>
          <div className="mt-4 text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
            Source data verification
          </div>
          <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Fields total" value={sdv.fields_total} />
            <Stat label="Auto-sourced" value={sdv.auto_sourced} />
            <Stat label="Confirmed" value={sdv.confirmed} tone="emerald" />
            <Stat label="Pending" value={sdv.pending} tone={sdv.pending > 0 ? "amber" : "emerald"} />
          </div>
        </>
      )}

      {compliance && (
        <>
          <div className="mt-4 text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
            Protocol compliance
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <div className="font-mono text-[14px] text-[color:var(--color-ink)]">
              {compliance.completed}/{compliance.required} activities completed
            </div>
            {compliance.missed.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1 text-[12px] text-amber-800">
                Missed: {compliance.missed.join(", ")}
              </div>
            )}
          </div>
        </>
      )}

      {deviations.length > 0 && (
        <>
          <div className="mt-4 text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
            Deviations
          </div>
          <div className="mt-2 flex flex-col gap-1.5">
            {deviations.map((d) => (
              <div
                key={d.id}
                className={`rounded-md border px-3 py-1.5 text-[13px] ${
                  d.severity === "major"
                    ? "border-rose-200 bg-rose-50 text-rose-800"
                    : "border-amber-200 bg-amber-50 text-amber-800"
                }`}
              >
                <span className="rounded-full bg-white/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
                  {d.severity}
                </span>{" "}
                {d.description}
              </div>
            ))}
          </div>
        </>
      )}

      {queries.length > 0 && (
        <>
          <div className="mt-4 text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
            Open queries
          </div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {queries.map((q, i) => (
              <li
                key={`${q.form}-${q.field}-${i}`}
                className="flex items-start gap-2 rounded-md border border-[color:var(--color-hair)] px-3 py-1.5 text-[13px] text-[color:var(--color-ink-body)]"
              >
                <span className="rounded bg-[color:var(--color-card-2)] px-1.5 py-0.5 font-mono text-[11px] text-[color:var(--color-ink-mute)]">
                  {q.form}.{q.field}
                </span>
                <span>{q.query}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {report.recommendation && (
        <div className="mt-4 rounded-md border border-[color:var(--color-hair-strong)] bg-[color:var(--color-card-2)] px-3 py-2.5 text-[13px] text-[color:var(--color-ink-body)]">
          <span className="font-semibold text-[color:var(--color-ink)]">Recommendation:</span>{" "}
          {report.recommendation}
        </div>
      )}
    </motion.section>
  )
}
