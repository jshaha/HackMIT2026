import { RadarChart } from "@/components/charts/radar-chart"
import { RadarGrid } from "@/components/charts/radar-grid"
import { RadarAxis } from "@/components/charts/radar-axis"
import { RadarLabels } from "@/components/charts/radar-labels"
import { RadarArea } from "@/components/charts/radar-area"
import type { HRV } from "@/lib/types"

interface HrvRadarProps {
  hrv: HRV
  size?: number
}

// Normalize each HRV metric to 0–100 against a healthy reference range so the
// polygon is readable. Missing metrics fall back to a low value.
function norm(v: number | null, min: number, max: number) {
  if (v == null) return 6
  return Math.max(4, Math.min(100, ((v - min) / (max - min)) * 100))
}

export function HrvRadar({ hrv, size = 260 }: HrvRadarProps) {
  const metrics = [
    { key: "sdnn", label: "SDNN" },
    { key: "rmssd", label: "RMSSD" },
    { key: "pnn50", label: "pNN50" },
    { key: "resp", label: "RESP" },
    { key: "balance", label: "LF/HF" },
  ]

  const data = [
    {
      label: "HRV",
      color: "#37444c",
      values: {
        sdnn: norm(hrv.sdnn, 10, 100),
        rmssd: norm(hrv.rmssd, 10, 80),
        pnn50: norm(hrv.pnn50, 0, 50),
        resp: norm(hrv.breathingrate, 8, 22),
        // LF/HF closest to ~1.0 is "balanced" → peak at 100
        balance: hrv.lf_hf == null ? 6 : Math.max(4, 100 - Math.min(100, Math.abs(hrv.lf_hf - 1) * 55)),
      },
    },
  ]

  return (
    <RadarChart data={data} metrics={metrics} size={size} levels={4} enterDurationMs={1300}>
      <RadarGrid />
      <RadarAxis />
      <RadarLabels />
      <RadarArea index={0} />
    </RadarChart>
  )
}
