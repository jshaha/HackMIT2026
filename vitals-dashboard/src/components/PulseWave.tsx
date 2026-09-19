import { useMemo } from "react"
import { motion } from "motion/react"

interface PulseWaveProps {
  data: number[]
  height?: number
  className?: string
  live?: boolean
}

/**
 * Pulse trace: the BVP waveform as a solid arterial-red line with a one-time
 * draw-on reveal. No glow, no infinite sweep — motion is tied to the reveal,
 * not decorative.
 */
export function PulseWave({ data, height = 128, className, live = false }: PulseWaveProps) {
  const W = 1000
  const H = height

  const path = useMemo(() => {
    if (!data.length) return ""
    const max = Math.max(...data.map(Math.abs)) || 1
    const step = W / (data.length - 1)
    const pad = 12
    return data
      .map((v, i) => {
        const x = i * step
        const y = H / 2 - (v / max) * (H / 2 - pad)
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(" ")
  }, [data, H])

  return (
    <div className={className}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} role="img" aria-label="pulse waveform">
        {/* baseline */}
        <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="var(--color-hair)" strokeWidth="1" />

        {/* one-time draw-on trace, solid arterial red */}
        <motion.path
          d={path}
          fill="none"
          stroke="var(--color-pulse)"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={live ? false : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: live ? 0 : 1.9, ease: [0.22, 1, 0.36, 1] }}
        />
      </svg>
    </div>
  )
}
