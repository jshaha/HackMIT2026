import { useMemo, useState } from "react"
import { motion } from "motion/react"
import type { AgendaItem, AgendaState } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

/** Stable id for an agenda item so decisions persist across polls even if the
 *  array order/length changes. */
function itemId(it: AgendaItem): string {
  return it.text
}

type Decision = "confirmed" | "dismissed"

/** The effective state of an item after applying the user's local decision. */
function effectiveStatus(it: AgendaItem, decision: Decision | undefined): "covered" | "suggested" | "pending" | "dismissed" {
  if (decision === "confirmed") return "covered"
  if (decision === "dismissed") return "dismissed"
  const s = it.status ?? (it.covered ? "covered" : "pending")
  // Never auto-check a suggested item — it must be confirmed by the user.
  return s
}

/** Visit agenda coverage — the always-listening tracker's output. Suggested
 *  items require an explicit Confirm; the user's decisions persist across polls. */
export function AgendaPanel({ agenda }: { agenda: AgendaState }) {
  // Local, poll-persistent decisions keyed by item id.
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})

  const rows = useMemo(
    () =>
      agenda.items
        .map((it) => ({ it, status: effectiveStatus(it, decisions[itemId(it)]) }))
        .filter((r) => r.status !== "dismissed"),
    [agenda.items, decisions],
  )

  // Covered count reflects local confirmations, not just what the JSON says.
  const covered = rows.filter((r) => r.status === "covered").length
  const total = rows.length

  function decide(it: AgendaItem, d: Decision) {
    setDecisions((prev) => ({ ...prev, [itemId(it)]: d }))
  }

  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease }}
      className="card mt-4 p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">Visit agenda</h2>
        <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
          {covered}/{total} covered · {Math.round(agenda.elapsed_s)}s listening
        </span>
      </div>
      <ul className="mt-4 flex flex-col gap-2.5">
        {rows.map(({ it, status }) => {
          const isCovered = status === "covered"
          const isSuggested = status === "suggested"
          return (
            <li
              key={itemId(it)}
              className={`flex items-start gap-2.5 rounded-md ${
                isSuggested ? "border border-amber-200 bg-amber-50 px-3 py-2.5" : ""
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] text-[11px] ${
                  isCovered
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
                    isCovered
                      ? "text-[color:var(--color-ink-mute)] line-through"
                      : isSuggested
                        ? "text-amber-900"
                        : "text-[color:var(--color-ink-body)]"
                  }`}
                >
                  {it.text}
                  {isSuggested && (
                    <span className="ml-2 rounded-full bg-amber-200 px-2 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-amber-800">
                      suggested
                    </span>
                  )}
                </div>
                {it.evidence && (isCovered || isSuggested) && (
                  <div
                    className={`mt-0.5 truncate font-mono text-[11px] ${
                      isSuggested ? "text-amber-700" : "text-emerald-700"
                    }`}
                  >
                    “{it.evidence}”
                    {isSuggested && it.similarity != null && (
                      <span className="ml-2 not-italic text-amber-600">match {(it.similarity * 100).toFixed(0)}%</span>
                    )}
                  </div>
                )}
                {isSuggested && (
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => decide(it, "confirmed")}
                      className="cursor-pointer rounded-md border border-emerald-400 bg-emerald-500 px-2.5 py-1 text-[12px] font-medium text-white transition hover:bg-emerald-600"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => decide(it, "dismissed")}
                      className="cursor-pointer rounded-md border border-[color:var(--color-hair-strong)] bg-[color:var(--color-card)] px-2.5 py-1 text-[12px] font-medium text-[color:var(--color-ink-body)] transition hover:border-[color:var(--color-ink-mute)]"
                    >
                      Dismiss
                    </button>
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
      {agenda.pending.length > 0 && (
        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
          <span className="font-medium">Still to raise:</span> {agenda.pending.join("; ")}
        </div>
      )}
    </motion.section>
  )
}
