import { motion } from "motion/react"
import { useAnimatedNumber } from "@/hooks/useAnimatedNumber"

interface StatTileProps {
  label: string
  value: number | null
  unit?: string
  decimals?: number
  delay?: number
  hint?: string
  live?: boolean
}

export function StatTile({ label, value, unit, decimals = 0, delay = 0, hint, live = false }: StatTileProps) {
  // Live mode: tick smoothly to the new value with no entrance delay, so the
  // tile updates in place instead of re-animating on every poll.
  const animated = useAnimatedNumber(value ?? 0, {
    decimals,
    delay: live ? 0 : delay + 0.15,
    duration: live ? 0.6 : 1,
  })

  return (
    <motion.div
      className="card px-4 py-4"
      initial={live ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: live ? 0 : delay, duration: live ? 0 : 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="eyebrow">{label}</div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="font-mono text-[26px] font-semibold tabular-nums text-[color:var(--color-ink)]">
          {value == null ? "—" : animated}
        </span>
        {unit && value != null && (
          <span className="font-mono text-[13px] text-[color:var(--color-ink-mute)]">{unit}</span>
        )}
      </div>
      {hint && <div className="mt-1.5 text-[12px] text-[color:var(--color-ink-mute)]">{hint}</div>}
    </motion.div>
  )
}
