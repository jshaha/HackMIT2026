import { RingChart } from "@/components/charts/ring-chart"
import { Ring } from "@/components/charts/ring"
import { useAnimatedNumber } from "@/hooks/useAnimatedNumber"

interface SqiRingProps {
  sqi: number // 0..1
  size?: number
  live?: boolean
}

// Sequential single-hue mapping: strong = arterial red, weaker = muted.
function sqiColor(sqi: number) {
  if (sqi >= 0.6) return "#c3372a"
  if (sqi >= 0.4) return "#b07d2b"
  return "#8a8f93"
}
function sqiLabel(sqi: number) {
  if (sqi >= 0.6) return "Strong"
  if (sqi >= 0.4) return "Fair"
  return "Weak"
}

/** Bklit RingChart as a signal-quality gauge, flat and glow-free. */
export function SqiRing({ sqi, size = 168, live = false }: SqiRingProps) {
  const color = sqiColor(sqi)
  const pct = useAnimatedNumber(sqi * 100, { duration: 1.4, decimals: 0, delay: 0.3, instant: live })
  const data = [{ label: "SQI", value: sqi, maxValue: 1, color }]

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <RingChart data={data} size={size} strokeWidth={11} startAngle={-Math.PI * 0.75} endAngle={Math.PI * 0.75}>
        <Ring index={0} showGlow={false} lineCap="round" />
      </RingChart>

      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="font-mono text-[34px] font-semibold" style={{ color }}>
          {pct}
          <span className="align-super text-base">%</span>
        </span>
        <span className="font-mono mt-1.5 text-[13px] font-medium" style={{ color }}>
          {sqiLabel(sqi)}
        </span>
      </div>
    </div>
  )
}
