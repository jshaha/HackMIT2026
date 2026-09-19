import { useMemo } from "react"
import { AreaChart } from "@/components/charts/area-chart"
import { Area } from "@/components/charts/area"
import { Grid } from "@/components/charts/grid"

interface HrTrendChartProps {
  series: { t: number; hr: number; sqi: number }[]
}

/** Bklit AreaChart plotting per-second heart rate over the capture window. */
export function HrTrendChart({ series }: HrTrendChartProps) {
  const data = useMemo(
    () =>
      series.map((p) => ({
        date: new Date(p.t * 1000),
        hr: p.hr,
      })),
    [series]
  )

  return (
    <AreaChart
      data={data}
      xDataKey="date"
      aspectRatio="16 / 6"
      animationDuration={1400}
      margin={{ top: 20, right: 16, bottom: 24, left: 30 }}
    >
      <Grid numTicksRows={4} />
      <Area
        dataKey="hr"
        fill="#c3372a"
        stroke="#c3372a"
        strokeWidth={2}
        fillOpacity={0.14}
        gradientToOpacity={0}
        gradientSpan={0.85}
        showMarkers={false}
      />
    </AreaChart>
  )
}
