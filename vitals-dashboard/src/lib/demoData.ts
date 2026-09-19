import type { Reading } from "./types"

// Generate a lifelike PPG pulse waveform: fundamental + dicrotic-notch harmonic,
// slow respiratory baseline drift, subtle beat-to-beat variability and noise.
function makeBVP(seconds: number, fs: number, bpm: number): number[] {
  const n = Math.round(seconds * fs)
  const hz = bpm / 60
  const out: number[] = new Array(n)
  let phase = 0
  for (let i = 0; i < n; i++) {
    const t = i / fs
    // small HRV wobble so beats aren't perfectly periodic
    const instHz = hz * (1 + 0.03 * Math.sin(2 * Math.PI * 0.18 * t))
    phase += (2 * Math.PI * instHz) / fs
    const systolic = Math.sin(phase)
    const dicrotic = 0.42 * Math.sin(2 * phase + 0.9)
    const resp = 0.16 * Math.sin(2 * Math.PI * 0.24 * t) // ~14 breaths/min drift
    const noise = 0.035 * (Math.sin(t * 91.3) + Math.sin(t * 143.7)) // deterministic
    out[i] = systolic + dicrotic + resp + noise
  }
  // normalize to ~[-1, 1]
  const max = Math.max(...out.map(Math.abs))
  return out.map((v) => +(v / max).toFixed(4))
}

function makeHrSeries(seconds: number, baseHr: number) {
  const s: { t: number; hr: number; sqi: number }[] = []
  for (let t = 4; t <= seconds; t += 1) {
    const hr = baseHr + 2.6 * Math.sin(t * 0.35) + 1.4 * Math.sin(t * 0.13)
    // SQI ramps up as the tracker locks on, then holds high
    const sqi = Math.min(0.93, 0.34 + (t / seconds) * 0.55 + 0.04 * Math.sin(t))
    s.push({ t, hr: +hr.toFixed(1), sqi: +sqi.toFixed(3) })
  }
  return s
}

function makeEmbedding(dim = 512): number[] {
  // deterministic pseudo-random unit-ish vector for the fingerprint viz
  const v: number[] = []
  let seed = 1337
  for (let i = 0; i < dim; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff
    v.push(+(((seed / 0x7fffffff) * 2 - 1) * 0.08).toFixed(4))
  }
  return v
}

const DURATION = 30
const FS = 30
const HR = 70.6

export const demoReading: Reading = {
  source: "iPhone (Continuity)",
  captured_at: new Date().toISOString(),
  duration_s: DURATION,
  fs: FS,
  hr: HR,
  sqi: 0.72,
  hrv: {
    sdnn: 48.3,
    rmssd: 41.7,
    pnn50: 22.4,
    lf_hf: 1.34,
    breathingrate: 14.2,
  },
  br: 14.2,
  bvp: makeBVP(DURATION, FS, HR),
  hr_series: makeHrSeries(DURATION, HR),
  voice: {
    duration_s: 15,
    sr: 16000,
    f0_mean_hz: 178.4,
    f0_std_hz: 31.2,
    jitter_pct: 3.1,
    shimmer_pct: 5.8,
    voiced_ratio: 0.61,
    pause_ratio: 0.14,
    speech_rate_hz: 3.6,
    rms_mean: 0.041,
    rms_std: 0.028,
    spectral_centroid_hz: 1820,
    arousal_index: 0.58,
    valence: 0.61,
    dominance: 0.54,
    emotional_state: "engaged / positive",
    emotion_source: "wav2vec2-msp-dim",
    fatigue_index: 0.29,
  },
  fatigue: {
    too_fatigued: false,
    fatigue_score: 0.29,
    confidence: 0.78,
    next_action: "continue",
    reasoning: "Fatigue markers within acceptable range — patient can continue.",
    key_factors: [
      "HR 70.6 BPM (normal)",
      "BR 14.2/min (normal)",
      "voice fatigue_index 0.29",
      "voice arousal_index 0.58 (ok)",
    ],
    engine: "rule-core",
    inputs: { heart_leg: true, voice_leg: true },
  },
  embedding: { n_segments: 3, dim: 512, mean: makeEmbedding(512) },
}
