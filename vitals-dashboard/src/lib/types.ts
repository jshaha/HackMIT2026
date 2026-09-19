// Shape of a reading exported from the Python pipeline (see export_reading.py).
export interface HRV {
  sdnn: number | null
  rmssd: number | null
  pnn50: number | null
  lf_hf: number | null
  breathingrate: number | null
}

// Voice-decoder leg (see voice_decoder.py).
export interface VoiceFeatures {
  duration_s: number
  sr: number
  f0_mean_hz: number
  f0_std_hz: number
  jitter_pct: number
  shimmer_pct: number
  voiced_ratio: number
  pause_ratio: number
  speech_rate_hz: number
  rms_mean: number
  rms_std: number
  spectral_centroid_hz: number
  arousal_index: number   // 0..1 (wav2vec2 SER, low = fatigued)
  valence?: number        // 0..1 (negative..positive)
  dominance?: number      // 0..1
  emotional_state?: string // e.g. "fatigued / low"
  emotion_source?: string  // "wav2vec2-msp-dim" | "acoustic-proxy"
  fatigue_index: number   // 0..1 derived
}

// Agentic-loop output (see fatigue_agent.py).
export interface FatigueDecision {
  too_fatigued: boolean | null
  fatigue_score: number | null
  confidence: number
  next_action: "continue" | "recheck" | "halt"
  reasoning: string
  key_factors: string[]
  engine: string          // "rule-core" | "openai:..." | "azure:..." | ...
  inputs: { heart_leg: boolean; voice_leg: boolean }
}

export interface Reading {
  source: string          // "iPhone (Continuity)" | "FaceTime HD" | ...
  captured_at: string     // ISO timestamp
  duration_s: number
  fs: number              // BVP sample rate (Hz)
  hr: number              // BPM
  br?: number | null      // breathing rate (breaths/min)
  sqi: number             // 0..1 signal quality
  hrv: HRV
  bvp: number[]           // pulse waveform (normalized), length = duration_s * fs
  hr_series?: { t: number; hr: number; sqi: number }[]  // per-second trend
  voice?: VoiceFeatures | null
  fatigue?: FatigueDecision | null
  embedding?: {
    n_segments: number
    dim: number
    mean: number[]        // mean 512-d PaPaGei feature vector
  }
}
