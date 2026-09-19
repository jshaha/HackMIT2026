import { useRef, useState } from "react"
import { AnimatePresence, motion } from "motion/react"
import { PulseWave } from "@/components/PulseWave"
import { SqiRing } from "@/components/SqiRing"
import { HrTrendChart } from "@/components/HrTrendChart"
import { HrvRadar } from "@/components/HrvRadar"
import { EmbeddingFingerprint } from "@/components/EmbeddingFingerprint"
import { StatTile } from "@/components/StatTile"
import { useAnimatedNumber } from "@/hooks/useAnimatedNumber"
import { demoReading } from "@/lib/demoData"
import type { Reading, FatigueDecision } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

function Section({ children, delay = 0, className = "" }: { children: React.ReactNode; delay?: number; className?: string }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.6, ease }}
      className={className}
    >
      {children}
    </motion.section>
  )
}

/** Small functional field label — sentence case, ≥13px, not a decorative kicker. */
function FieldLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-[13px] font-medium text-[color:var(--color-ink-mute)]">{children}</div>
}

/** The agentic-loop verdict — the pipeline's headline output. */
function FatigueBanner({ fatigue }: { fatigue: FatigueDecision }) {
  const tf = fatigue.too_fatigued
  const theme =
    tf === true
      ? { ring: "border-rose-300", bg: "bg-rose-50", ink: "text-rose-700", dot: "bg-rose-500", label: "Too fatigued" }
      : tf === false
        ? { ring: "border-emerald-300", bg: "bg-emerald-50", ink: "text-emerald-700", dot: "bg-emerald-500", label: "OK to continue" }
        : { ring: "border-amber-300", bg: "bg-amber-50", ink: "text-amber-700", dot: "bg-amber-500", label: "Inconclusive" }
  const score = fatigue.fatigue_score
  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.06, duration: 0.6, ease }}
      className={`mt-6 rounded-xl border ${theme.ring} ${theme.bg} p-6`}
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${theme.dot}`} />
          <div>
            <div className="text-[12px] font-medium uppercase tracking-wide text-[color:var(--color-ink-mute)]">
              Fatigue decision
            </div>
            <div className={`font-display text-[24px] font-bold leading-tight ${theme.ink}`}>{theme.label}</div>
          </div>
        </div>
        <div className="flex items-center gap-6 font-mono text-[13px] text-[color:var(--color-ink-body)]">
          <div>
            <div className="text-[color:var(--color-ink-mute)]">Fatigue score</div>
            <div className="text-[18px] tabular-nums">{score == null ? "—" : score.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-[color:var(--color-ink-mute)]">Confidence</div>
            <div className="text-[18px] tabular-nums">{(fatigue.confidence * 100).toFixed(0)}%</div>
          </div>
          <div>
            <div className="text-[color:var(--color-ink-mute)]">Next action</div>
            <div className={`text-[18px] font-semibold uppercase ${theme.ink}`}>{fatigue.next_action}</div>
          </div>
        </div>
      </div>
      <p className="mt-3 text-[14px] leading-relaxed text-[color:var(--color-ink-body)]">{fatigue.reasoning}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {fatigue.key_factors.map((f, i) => (
          <span
            key={i}
            className="rounded-md border border-[color:var(--color-hair)] bg-[color:var(--color-card)] px-2.5 py-1 font-mono text-[11px] text-[color:var(--color-ink-body)]"
          >
            {f}
          </span>
        ))}
      </div>
      <div className="mt-3 font-mono text-[11px] text-[color:var(--color-ink-mute)]">
        engine: {fatigue.engine} · legs: heart={String(fatigue.inputs.heart_leg)} voice={String(fatigue.inputs.voice_leg)}
      </div>
    </motion.section>
  )
}

export default function App() {
  const [reading, setReading] = useState<Reading>(demoReading)
  const [isReal, setIsReal] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const hr = useAnimatedNumber(reading.hr, { decimals: 1, duration: 1.4, delay: 0.4 })

  function apply(data: Reading) {
    setReading({ ...demoReading, ...data, hrv: { ...demoReading.hrv, ...data.hrv } })
    setIsReal(true)
  }

  async function loadPublicReading() {
    try {
      const res = await fetch("/reading.json", { cache: "no-store" })
      if (!res.ok) throw new Error("no file")
      apply((await res.json()) as Reading)
    } catch {
      fileRef.current?.focus()
      alert("No /reading.json found yet.\nRun:  conda activate rppg && python export_reading.py --in iphone_bvp.npz")
    }
  }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    const r = new FileReader()
    r.onload = () => {
      try {
        apply(JSON.parse(String(r.result)) as Reading)
      } catch {
        alert("Could not parse that JSON.")
      }
    }
    r.readAsText(f)
  }

  const captured = new Date(reading.captured_at)
  const locked = reading.sqi >= 0.5

  return (
    <div className="mx-auto max-w-[1180px] px-6 py-10 md:px-10 md:py-14">
      {/* ── header ─────────────────────────────────────────── */}
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease }}
        className="flex flex-wrap items-end justify-between gap-5 border-b border-[color:var(--color-hair)] pb-6"
      >
        <div className="max-w-[46ch]">
          <h1 className="font-display text-[30px] font-bold tracking-tight text-[color:var(--color-ink)]">
            Vitals
          </h1>
          <p className="mt-1.5 text-[14px] text-[color:var(--color-ink-body)]">
            Heart rate, HRV and respiration read from a face video — no wearable —
            with a PaPaGei feature embedding.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <span className="font-mono rounded-md border border-[color:var(--color-hair)] bg-[color:var(--color-card)] px-3 py-2 text-[12px] text-[color:var(--color-ink-body)]">
            {isReal ? "Real capture" : "Demo signal"}
          </span>
          <button
            onClick={loadPublicReading}
            className="cursor-pointer rounded-md border border-[color:var(--color-pulse)] bg-[color:var(--color-pulse)] px-3.5 py-2 text-[13px] font-medium text-white transition hover:bg-[color:var(--color-pulse-deep)] hover:border-[color:var(--color-pulse-deep)]"
          >
            Load reading
          </button>
          <button
            onClick={() => fileRef.current?.click()}
            className="cursor-pointer rounded-md border border-[color:var(--color-hair-strong)] bg-[color:var(--color-card)] px-3.5 py-2 text-[13px] font-medium text-[color:var(--color-ink-body)] transition hover:border-[color:var(--color-ink-mute)]"
          >
            Upload JSON
          </button>
          <input ref={fileRef} type="file" accept="application/json" className="hidden" onChange={onFile} />
        </div>
      </motion.header>

      {/* ── agentic-loop verdict (headline output) ─────────── */}
      {reading.fatigue && <FatigueBanner fatigue={reading.fatigue} />}

      {/* ── primary metric band (flat card, two columns — not nested) ── */}
      <Section delay={0.1} className="card mt-4 overflow-hidden">
        <div className="grid md:grid-cols-[minmax(0,1fr)_300px]">
          <div className="flex flex-col gap-5 p-7 md:p-8">
            <div className="flex items-center justify-between">
              <FieldLabel>Heart rate</FieldLabel>
              <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
                {reading.source} · {captured.toLocaleTimeString()} · {reading.duration_s}s
              </span>
            </div>
            <div className="flex items-baseline gap-3">
              <span className="font-display text-[104px] font-bold leading-[0.82] tracking-tight tabular-nums text-[color:var(--color-pulse)]">
                {hr.toFixed(1)}
              </span>
              <span className="font-mono text-[17px] text-[color:var(--color-ink-mute)]">bpm</span>
            </div>
            <PulseWave data={reading.bvp} height={116} className="mt-1" />
          </div>

          {/* sibling column, divided by a hairline (not a card-in-card) */}
          <div className="flex flex-col items-center justify-center gap-4 border-t border-[color:var(--color-hair)] bg-[color:var(--color-card-2)] p-7 md:border-l md:border-t-0">
            <FieldLabel>Signal quality</FieldLabel>
            <SqiRing sqi={reading.sqi} />
            <p className="max-w-[24ch] text-center text-[13px] leading-relaxed text-[color:var(--color-ink-mute)]">
              {locked ? "Lock acquired — HRV metrics computed." : "Low confidence — hold still, improve lighting."}
            </p>
          </div>
        </div>
      </Section>

      {/* ── HRV / respiration metrics ──────────────────────── */}
      <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-5">
        <StatTile label="Respiration" value={reading.br ?? reading.hrv.breathingrate} unit="/min" decimals={1} delay={0.24} hint="breathing rate" />
        <StatTile label="SDNN" value={reading.hrv.sdnn} unit="ms" decimals={1} delay={0.29} hint="HRV spread" />
        <StatTile label="RMSSD" value={reading.hrv.rmssd} unit="ms" decimals={1} delay={0.34} hint="beat-to-beat" />
        <StatTile label="pNN50" value={reading.hrv.pnn50} unit="%" decimals={1} delay={0.39} hint="NN over 50ms" />
        <StatTile label="LF / HF" value={reading.hrv.lf_hf} decimals={2} delay={0.44} hint="autonomic balance" />
      </div>

      {/* ── voice-decoder leg (emotional state / fatigue markers) ── */}
      {reading.voice && (
        <Section delay={0.28} className="mt-4">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div className="flex items-center gap-2.5">
              <FieldLabel>Voice decoder</FieldLabel>
              {reading.voice.emotional_state && (
                <span className="rounded-full border border-[color:var(--color-hair-strong)] bg-[color:var(--color-card-2)] px-2.5 py-0.5 text-[12px] font-medium text-[color:var(--color-ink-body)]">
                  {reading.voice.emotional_state}
                </span>
              )}
            </div>
            <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
              {reading.voice.emotion_source ?? "voice"} · {reading.voice.duration_s}s @ {(reading.voice.sr / 1000).toFixed(0)} kHz
            </span>
          </div>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
            <StatTile label="Arousal" value={reading.voice.arousal_index} decimals={2} delay={0.30} hint="low = fatigued" />
            <StatTile label="Valence" value={reading.voice.valence ?? null} decimals={2} delay={0.33} hint="neg ↔ pos" />
            <StatTile label="Fatigue (voice)" value={reading.voice.fatigue_index} decimals={2} delay={0.36} hint="vocal fatigue" />
            <StatTile label="Speech rate" value={reading.voice.speech_rate_hz} unit="syl/s" decimals={1} delay={0.39} hint="tempo" />
            <StatTile label="Pauses" value={reading.voice.pause_ratio} decimals={2} delay={0.42} hint="silence ratio" />
            <StatTile label="Pitch F0" value={reading.voice.f0_mean_hz} unit="Hz" decimals={0} delay={0.45} hint="mean pitch" />
          </div>
        </Section>
      )}

      {/* ── charts ─────────────────────────────────────────── */}
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Section delay={0.34} className="card p-7">
          <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">Heart rate over the capture</h2>
          <p className="mt-1 text-[13px] text-[color:var(--color-ink-mute)]">Per-second estimate, {reading.duration_s}s window.</p>
          <div className="mt-5">
            <HrTrendChart series={reading.hr_series ?? []} />
          </div>
        </Section>

        <Section delay={0.4} className="card flex flex-col p-7">
          <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">HRV profile</h2>
          <p className="mt-1 text-[13px] text-[color:var(--color-ink-mute)]">Normalized against healthy ranges.</p>
          <div className="flex flex-1 items-center justify-center pt-2">
            <HrvRadar hrv={reading.hrv} />
          </div>
        </Section>
      </div>

      {/* ── PaPaGei embedding ──────────────────────────────── */}
      <AnimatePresence>
        {reading.embedding && (
          <Section delay={0.46} className="card mt-4 p-7">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">
                PaPaGei embedding · {reading.embedding.dim}-dimensional feature vector
              </h2>
              <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
                {reading.embedding.n_segments} × 10s segments, mean vector
              </span>
            </div>
            <div className="mt-5">
              <EmbeddingFingerprint vector={reading.embedding.mean} />
            </div>
            <p className="mt-4 max-w-[75ch] text-[13px] leading-relaxed text-[color:var(--color-ink-mute)]">
              The downstream feature vector from the PaPaGei foundation model — the input to
              cardiovascular, sleep, and wellbeing prediction heads. Cell intensity encodes each
              dimension's magnitude.
            </p>
          </Section>
        )}
      </AnimatePresence>

      {/* ── footer ─────────────────────────────────────────── */}
      <footer className="mt-10 flex flex-wrap items-center justify-between gap-2 border-t border-[color:var(--color-hair)] pt-5 text-[12px] text-[color:var(--color-ink-mute)]">
        <span className="font-mono">rPPG-Toolbox · open-rppg · PaPaGei</span>
        <span>
          SQI {(reading.sqi * 100).toFixed(0)}% · {reading.fs.toFixed(0)} Hz · contactless estimate, not medical grade
        </span>
      </footer>
    </div>
  )
}
