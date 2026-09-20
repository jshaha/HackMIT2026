import { motion } from "motion/react"
import type { FatigueDecision } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

/** Normalize the headline recommendation, falling back to the older
 *  too_fatigued / next_action fields when trial_recommendation is absent. */
function resolveRecommendation(f: FatigueDecision): "continue" | "pause" | "stop" {
  if (f.trial_recommendation) return f.trial_recommendation
  if (f.too_fatigued === true || f.next_action === "halt") return "stop"
  if (f.next_action === "recheck") return "pause"
  return "continue"
}

const REC_THEME = {
  stop: { ring: "border-rose-300", bg: "bg-rose-50", ink: "text-rose-700", dot: "bg-rose-500", label: "Stop the trial", bar: "bg-rose-500" },
  pause: { ring: "border-amber-300", bg: "bg-amber-50", ink: "text-amber-700", dot: "bg-amber-500", label: "Pause & recheck", bar: "bg-amber-500" },
  continue: { ring: "border-emerald-300", bg: "bg-emerald-50", ink: "text-emerald-700", dot: "bg-emerald-500", label: "Continue", bar: "bg-emerald-500" },
} as const

/** The agentic-loop verdict — the pipeline's headline output. */
export function FatigueBanner({ fatigue }: { fatigue: FatigueDecision }) {
  const rec = resolveRecommendation(fatigue)
  const theme = REC_THEME[rec]
  const score = fatigue.fatigue_score
  const contributions = fatigue.contributions ?? []
  // For the bar widths, scale each contribution against the largest one present.
  const maxContrib = contributions.reduce((m, c) => Math.max(m, Math.abs(c.contribution)), 0) || 1

  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.06, duration: 0.6, ease }}
      className={`mt-6 rounded-xl border ${theme.ring} ${theme.bg} p-6`}
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${theme.dot}`} />
          <div>
            <div className="text-[12px] font-medium uppercase tracking-wide text-[color:var(--color-ink-mute)]">
              Trial recommendation
            </div>
            <div className={`font-display text-[26px] font-bold leading-tight ${theme.ink}`}>{theme.label}</div>
          </div>
        </div>
        <div className="flex items-center gap-6 font-mono text-[13px] text-[color:var(--color-ink-body)]">
          <div>
            <div className="text-[color:var(--color-ink-mute)]">Fatigue score</div>
            <div className="text-[18px] tabular-nums">{score == null ? "—" : score.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-[color:var(--color-ink-mute)]">Confidence</div>
            <div className="text-[18px] tabular-nums">{(fatigue.confidence * 100).toFixed(0)}%</div>
          </div>
          <div>
            <div className="text-[color:var(--color-ink-mute)]">Next action</div>
            <div className={`text-[18px] font-semibold uppercase ${theme.ink}`}>{fatigue.next_action}</div>
          </div>
        </div>
      </div>

      <p className="mt-3 text-[14px] leading-relaxed text-[color:var(--color-ink-body)]">{fatigue.reasoning}</p>

      {/* Contributions breakdown — which features drove the score, and how much. */}
      {contributions.length > 0 && (
        <div className="mt-4 rounded-lg border border-[color:var(--color-hair)] bg-[color:var(--color-card)] p-4">
          <div className="text-[12px] font-medium uppercase tracking-wide text-[color:var(--color-ink-mute)]">
            Score breakdown
          </div>
          <ul className="mt-3 flex flex-col gap-2.5">
            {contributions.map((c, i) => (
              <li key={i} className="grid grid-cols-[minmax(0,1fr)_120px] items-center gap-3">
                <div className="min-w-0">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[13px] font-medium text-[color:var(--color-ink-body)]">{c.feature}</span>
                    <span className="shrink-0 font-mono text-[11px] text-[color:var(--color-ink-mute)]">
                      w{c.weight.toFixed(2)} · val {c.value}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--color-card-2)]">
                    <div
                      className={`h-full rounded-full ${theme.bar}`}
                      style={{ width: `${Math.min(100, (Math.abs(c.contribution) / maxContrib) * 100)}%` }}
                    />
                  </div>
                  {c.note && <div className="mt-0.5 text-[11px] text-[color:var(--color-ink-mute)]">{c.note}</div>}
                </div>
                <div className="text-right font-mono text-[13px] tabular-nums text-[color:var(--color-ink-body)]">
                  +{c.contribution.toFixed(2)}
                </div>
              </li>
            ))}
          </ul>
          {fatigue.weights_rationale && (
            <p className="mt-3 text-[12px] leading-relaxed text-[color:var(--color-ink-mute)]">
              {fatigue.weights_rationale}
            </p>
          )}
        </div>
      )}

      {/* Legacy key factors, still shown when no contributions breakdown is present. */}
      {contributions.length === 0 && fatigue.key_factors && fatigue.key_factors.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {fatigue.key_factors.map((f, i) => (
            <span
              key={i}
              className="rounded-md border border-[color:var(--color-hair)] bg-[color:var(--color-card)] px-2.5 py-1 font-mono text-[11px] text-[color:var(--color-ink-body)]"
            >
              {f}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 font-mono text-[11px] text-[color:var(--color-ink-mute)]">
        engine: {fatigue.engine} · legs: heart={String(fatigue.inputs.heart_leg)} voice={String(fatigue.inputs.voice_leg)}
      </div>
    </motion.section>
  )
}

/** Full-page pulsing red border + fixed alert banner shown when the patient is
 *  too fatigued or the recommendation is to stop. Unmissable but tasteful. */
export function FatigueAlertOverlay({ fatigue }: { fatigue: FatigueDecision }) {
  const rec = resolveRecommendation(fatigue)
  const active = fatigue.too_fatigued === true || rec === "stop"
  if (!active) return null
  return (
    <>
      {/* pulsing red border framing the whole viewport */}
      <motion.div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-40"
        style={{ boxShadow: "inset 0 0 0 6px rgb(244 63 94)" }}
        animate={{ opacity: [0.35, 1, 0.35] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* fixed alert banner */}
      <motion.div
        role="alert"
        className="fixed inset-x-0 top-0 z-50 flex justify-center px-4 py-3"
        initial={{ y: -60, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.5, ease }}
      >
        <motion.div
          className="flex items-center gap-3 rounded-full border border-rose-400 bg-rose-600 px-5 py-2.5 text-[14px] font-semibold text-white shadow-lg"
          animate={{ scale: [1, 1.03, 1] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
        >
          <span className="text-[16px]">⚠</span>
          <span>Patient too fatigued — recommend pausing the trial</span>
        </motion.div>
      </motion.div>
    </>
  )
}
