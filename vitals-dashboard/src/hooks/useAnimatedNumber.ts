import { useEffect, useState } from "react"
import { animate, useMotionValue } from "motion/react"

/** Springs a number from 0 (or previous) to `value`, returns the live display value. */
export function useAnimatedNumber(value: number, opts?: { duration?: number; decimals?: number; delay?: number }) {
  const { duration = 1.2, decimals = 0, delay = 0 } = opts ?? {}
  const mv = useMotionValue(0)
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    const controls = animate(mv, value, {
      duration,
      delay,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setDisplay(+v.toFixed(decimals)),
    })
    return controls.stop
  }, [value, duration, decimals, delay, mv])

  return display
}
