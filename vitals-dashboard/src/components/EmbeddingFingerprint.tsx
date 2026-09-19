import { useMemo } from "react"
import { motion } from "motion/react"

interface EmbeddingFingerprintProps {
  vector: number[]
  cols?: number
}

// Sequential single-hue scale: magnitude → arterial-red intensity on paper.
// (A real sequential data-viz ramp, not the cyan/violet AI palette.)
function cellColor(v: number, max: number) {
  const t = Math.min(1, Math.abs(v) / (max || 1))
  // interpolate paper → arterial red
  const r = Math.round(246 + (195 - 246) * t)
  const g = Math.round(248 + (55 - 248) * t)
  const b = Math.round(249 + (42 - 249) * t)
  return `rgb(${r}, ${g}, ${b})`
}

/** PaPaGei 512-d feature vector rendered as a sequential heat-grid. */
export function EmbeddingFingerprint({ vector, cols = 32 }: EmbeddingFingerprintProps) {
  const max = useMemo(() => Math.max(...vector.map((v) => Math.abs(v))) || 1, [vector])
  const rows = Math.ceil(vector.length / cols)

  return (
    <div
      className="grid gap-[3px] rounded-md border border-[color:var(--color-hair)] p-2"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      aria-label={`${vector.length}-dimensional embedding`}
    >
      {vector.map((v, i) => {
        const r = Math.floor(i / cols)
        const c = i % cols
        return (
          <motion.div
            key={i}
            className="aspect-square rounded-[2px]"
            style={{ background: cellColor(v, max) }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{
              delay: 0.3 + ((r + c) / (rows + cols)) * 0.6,
              duration: 0.4,
              ease: [0.16, 1, 0.3, 1],
            }}
          />
        )
      })}
    </div>
  )
}
