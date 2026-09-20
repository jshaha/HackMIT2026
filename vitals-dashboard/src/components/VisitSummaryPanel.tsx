import { motion } from "motion/react"
import type { VisitSummary } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

function fmtDuration(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

/** After-visit summary — narrative, coverage and the vitals/fatigue highlight. */
export function VisitSummaryPanel({ summary }: { summary: VisitSummary }) {
  const { vitals } = summary
  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease }}
      className="card mt-4 p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">After-visit summary</h2>
        <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
          {summary.patient} · {fmtDuration(summary.duration_s)}
        </span>
      </div>

      <p className="mt-3 max-w-[80ch] text-[14px] leading-relaxed text-[color:var(--color-ink-body)]">
        {summary.summary}
      </p>

      {summary.emotional_arc && (
        <p className="mt-2 max-w-[80ch] text-[13px] italic leading-relaxed text-[color:var(--color-ink-mute)]">
          Emotional arc: {summary.emotional_arc}
        </p>
      )}

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
          <div className="text-[12px] font-medium uppercase tracking-wide text-emerald-700">Topics covered</div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {summary.topics_covered.length === 0 ? (
              <li className="text-[13px] text-emerald-800/60">—</li>
            ) : (
              summary.topics_covered.map((t, i) => (
                <li key={i} className="flex items-start gap-2 text-[13px] text-emerald-900">
                  <span className="mt-0.5 text-emerald-500">✓</span>
                  <span>{t}</span>
                </li>
              ))
            )}
          </ul>
        </div>
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <div className="text-[12px] font-medium uppercase tracking-wide text-amber-700">Topics missed</div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {summary.topics_missed.length === 0 ? (
              <li className="text-[13px] text-amber-800/60">None — full agenda covered</li>
            ) : (
              summary.topics_missed.map((t, i) => (
                <li key={i} className="flex items-start gap-2 text-[13px] text-amber-900">
                  <span className="mt-0.5 text-amber-500">•</span>
                  <span>{t}</span>
                </li>
              ))
            )}
          </ul>
        </div>
      </div>

      <div className="mt-4 grid gap-3 rounded-lg border border-[color:var(--color-hair)] bg-[color:var(--color-card-2)] p-4 sm:grid-cols-3">
        <div>
          <div className="text-[12px] text-[color:var(--color-ink-mute)]">Avg heart rate</div>
          <div className="font-mono text-[18px] tabular-nums text-[color:var(--color-ink)]">
            {vitals.hr_avg == null ? "—" : `${vitals.hr_avg.toFixed(1)} bpm`}
          </div>
        </div>
        <div>
          <div className="text-[12px] text-[color:var(--color-ink-mute)]">Peak fatigue</div>
          <div className="font-mono text-[18px] tabular-nums text-[color:var(--color-ink)]">
            {vitals.fatigue_peak == null ? "—" : vitals.fatigue_peak.toFixed(2)}
          </div>
        </div>
        <div>
          <div className="text-[12px] text-[color:var(--color-ink-mute)]">Final recommendation</div>
          <div className="text-[14px] font-semibold text-[color:var(--color-ink-body)]">
            {vitals.final_recommendation ?? "—"}
          </div>
        </div>
      </div>
    </motion.section>
  )
}
