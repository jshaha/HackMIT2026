"use client"

import { cn } from "@/lib/utils"

export interface ShimmeringTextProps {
  text: string
  className?: string
}

/**
 * Minimal shimmering text (chart loading label dependency).
 * Uses a moving gradient masked onto the text.
 */
export function ShimmeringText({ text, className }: ShimmeringTextProps) {
  return (
    <span
      className={cn("shimmer-text", className)}
      style={{
        backgroundImage:
          "linear-gradient(90deg, var(--color, #5c706a) 0%, var(--shimmering-color, #e6f2ec) 50%, var(--color, #5c706a) 100%)",
        backgroundSize: "200% 100%",
        WebkitBackgroundClip: "text",
        backgroundClip: "text",
        color: "transparent",
        animation: "shimmerText 1.6s linear infinite",
      }}
    >
      {text}
    </span>
  )
}

export default ShimmeringText
