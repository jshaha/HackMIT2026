import { motion } from "motion/react"
import type { SoaActivity, SoaState } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

/** Schedule of Activities coverage for the current visit — each required
 *  activity shown as done / suggested / pending, with a done/total count and an
 *  in-window badge. Read-only (the automated SoA tracker's live view). */
export function SoaPanel({ soa }: { soa: SoaState }) {
  const activities = soa.activities ?? []
  const done = soa.done ?? activities.filter((a) => a.status === "done").length
  const total = soa.required_total ?? (activities.filter((a) => a.required).length || activities.length)

  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease }}
      className="card mt-4 p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">Schedule of Activities</h2>
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              soa.in_window
                ? "bg-emerald-50 text-emerald-700"
                : "bg-amber-50 text-amber-700"
            }`}
          >
            {soa.in_window ? "in window" : "out of window"}
          </span>
        </div>
        <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
          {soa.study} · {soa.subject} · {soa.visit} · {done}/{total} done
        </span>
      </div>

      <ul className="mt-4 flex flex-col gap-2.5">
        {activities.map((a) => (
          <SoaRow key={a.id} activity={a} />
        ))}
      </ul>
    </motion.section>
  )
}

function SoaRow({ activity: a }: { activity: SoaActivity }) {
  const isDone = a.status === "done"
  const isSuggested = a.status === "suggested"
  return (
    <li
      className={`flex items-start gap-2.5 rounded-md ${
        isSuggested ? "border border-amber-200 bg-amber-50 px-3 py-2.5" : ""
      }`}
    >
      <span
        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] text-[11px] ${
          isDone
            ? "bg-emerald-500 text-white"
            : isSuggested
              ? "border border-amber-400 text-transparent"
              : "border border-[color:var(--color-hair-strong)] text-transparent"
        }`}
      >
        ✓
      </span>
      <div className="min-w-0 flex-1">
        <div
          className={`text-[14px] ${
            isDone
              ? "text-[color:var(--color-ink-mute)]"
              : isSuggested
                ? "text-amber-900"
                : "text-[color:var(--color-ink-body)]"
          }`}
        >
          {a.name}
          {a.form && (
            <span className="ml-2 rounded bg-[color:var(--color-card-2)] px-1.5 py-0.5 align-middle font-mono text-[10px] text-[color:var(--color-ink-mute)]">
              {a.form}
            </span>
          )}
          {isSuggested && (
            <span className="ml-2 rounded-full bg-amber-200 px-2 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-amber-800">
              suggested
            </span>
          )}
          {a.status === "pending" && (
            <span className="ml-2 rounded-full border border-[color:var(--color-hair-strong)] px-2 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
              pending
            </span>
          )}
        </div>
        {a.evidence && (isDone || isSuggested) && (
          <div
            className={`mt-0.5 truncate font-mono text-[11px] ${
              isSuggested ? "text-amber-700" : "text-emerald-700"
            }`}
          >
            {a.evidence}
            {a.source && <span className="ml-2 text-[color:var(--color-ink-mute)]">via {a.source}</span>}
          </div>
        )}
      </div>
    </li>
  )
}
