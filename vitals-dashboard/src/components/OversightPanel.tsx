import { motion } from "motion/react"
import type { OversightCheck, OversightState } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

const OVERALL = {
  "on-track": { label: "On track", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  attention: { label: "Attention", cls: "bg-amber-50 text-amber-800 border-amber-300" },
  deviation: { label: "Deviation", cls: "bg-rose-50 text-rose-700 border-rose-300" },
} as const

const CHECK = {
  pass: { badge: "bg-emerald-100 text-emerald-700", dot: "bg-emerald-500", row: "" },
  warn: {
    badge: "bg-amber-100 text-amber-800",
    dot: "bg-amber-500",
    row: "border border-amber-200 bg-amber-50",
  },
  fail: {
    badge: "bg-rose-100 text-rose-700",
    dot: "bg-rose-500",
    row: "border border-rose-200 bg-rose-50",
  },
} as const

/** Automated oversight (the standing CRA/monitor). Overall status badge, the
 *  per-activity checks (warn/fail made prominent), and any recorded deviations. */
export function OversightPanel({ oversight }: { oversight: OversightState }) {
  const overall = OVERALL[oversight.overall] ?? OVERALL["on-track"]
  const checks = oversight.checks ?? []
  const deviations = oversight.deviations ?? []

  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease }}
      className="card mt-4 p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">Automated oversight</h2>
          <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${overall.cls}`}>
            {overall.label}
          </span>
        </div>
        <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
          {oversight.subject} · {oversight.visit}
        </span>
      </div>

      <ul className="mt-4 flex flex-col gap-2">
        {checks.map((c, i) => (
          <CheckRow key={c.activity ?? i} check={c} />
        ))}
      </ul>

      {deviations.length > 0 && (
        <div className="mt-4 flex flex-col gap-2">
          <div className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
            Deviations
          </div>
          {deviations.map((d) => (
            <div
              key={d.id}
              className={`rounded-md border px-3 py-2 text-[13px] ${
                d.severity === "major"
                  ? "border-rose-200 bg-rose-50 text-rose-800"
                  : "border-amber-200 bg-amber-50 text-amber-800"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-white/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
                  {d.severity}
                </span>
                {d.activity && <span className="font-mono text-[11px] opacity-80">{d.activity}</span>}
              </div>
              <div className="mt-1 font-medium">{d.description}</div>
              {d.guidance && <div className="mt-0.5 opacity-90">{d.guidance}</div>}
            </div>
          ))}
        </div>
      )}
    </motion.section>
  )
}

function CheckRow({ check: c }: { check: OversightCheck }) {
  const s = CHECK[c.status] ?? CHECK.pass
  const prominent = c.status !== "pass"
  return (
    <li className={`flex items-start gap-2.5 rounded-md px-3 py-2 ${s.row}`}>
      <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${s.dot}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${s.badge}`}>
            {c.status}
          </span>
          {c.activity && (
            <span className="font-mono text-[11px] text-[color:var(--color-ink-mute)]">{c.activity}</span>
          )}
        </div>
        {c.detail && (
          <div
            className={`mt-1 text-[13px] ${
              prominent ? "font-medium text-[color:var(--color-ink)]" : "text-[color:var(--color-ink-body)]"
            }`}
          >
            {c.detail}
          </div>
        )}
        {c.guidance && (
          <div className={`mt-0.5 text-[12px] ${prominent ? "text-amber-800" : "text-[color:var(--color-ink-mute)]"}`}>
            → {c.guidance}
          </div>
        )}
      </div>
    </li>
  )
}
