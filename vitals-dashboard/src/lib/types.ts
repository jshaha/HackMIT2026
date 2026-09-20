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
  speech_s?: number | null          // seconds of speech kept after VAD
  speaker_similarity?: number | null // cosine to enrolled patient (0..1)
  is_patient?: boolean | null        // matched the enrolled patient?
}

// Emotional state read from the face video (see face_emotion.py).
export interface FaceEmotion {
  arousal: number | null
  valence: number | null
  emotional_state: string
  source: string
}

// One driver of the fatigue score in the revamped decision.
export interface FatigueContribution {
  feature: string
  value: number
  weight: number
  contribution: number
  note: string
}

// Agentic-loop output (see fatigue_agent.py).
export interface FatigueDecision {
  too_fatigued: boolean | null
  fatigue_score: number | null
  confidence: number
  next_action: "continue" | "recheck" | "halt"
  reasoning: string
  key_factors?: string[]  // legacy — superseded by contributions
  engine: string          // "rule-core" | "openai:..." | "azure:..." | ...
  inputs: { heart_leg?: boolean; voice_leg?: boolean; [k: string]: unknown }
  // Revamped fields (guard with null checks — may be absent from older payloads).
  trial_recommendation?: "continue" | "pause" | "stop"
  contributions?: FatigueContribution[]
  weights_rationale?: string
}

// Visit agenda coverage (see conversation_tracker.py).
export interface AgendaItem {
  text: string
  covered: boolean
  evidence: string | null
  covered_at: number | null
  status?: "pending" | "suggested" | "covered"
  similarity?: number | null
}

export interface AgendaState {
  patient: string
  updated_at: number
  elapsed_s: number
  covered: number
  total: number
  items: AgendaItem[]
  pending: string[]
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
  face_emotion?: FaceEmotion | null
  fatigue?: FatigueDecision | null
  embedding?: {
    n_segments: number
    dim: number
    mean: number[]        // mean 512-d PaPaGei feature vector
  }
}

// ── Clinical-trial (Veeva/EDC) layer — see VEEVA_CONTRACT.md ──────────────

// One eCRF field with ALCOA provenance. Guard every field with null checks.
export interface CrfField {
  id: string
  label: string
  type: string // "number" | "text" | "datetime" | "enum" | "bool" | ...
  value?: string | number | boolean | null
  status: "empty" | "draft" | "confirmed"
  required?: boolean
  source?: string | null       // e.g. "contactless-rppg" | "fatigue-model" | "transcript-extraction"
  captured_at?: string | null  // ISO
  confidence?: number | null   // 0..1
  signer?: string | null
  signed_at?: string | null
}

// One eCRF form instance (edc_forms.json -> forms[]).
export interface CrfForm {
  form: string   // "VS" | "AE" | "CM" | "PRO" | "IC" | "PE"
  name: string
  subject?: string
  visit?: string
  status: "empty" | "draft" | "confirmed"
  fields: CrfField[]
}

// public/edc_forms.json
export interface EdcForms {
  study: string
  subject: string
  visit: string
  updated_at: number
  forms: CrfForm[]
}

// One Schedule-of-Activities row (soa_state.json -> activities[]).
export interface SoaActivity {
  id: string
  name: string
  form?: string
  required?: boolean
  status: "done" | "suggested" | "pending"
  evidence?: string | null
  source?: string | null // "edc" | "transcript"
}

// public/soa_state.json
export interface SoaState {
  study: string
  subject: string
  visit: string
  in_window?: boolean
  updated_at: number
  required_total: number
  done: number
  activities: SoaActivity[]
  pending?: string[]
}

// One live oversight check (oversight_state.json -> checks[]).
export interface OversightCheck {
  activity?: string
  status: "pass" | "warn" | "fail"
  detail?: string
  guidance?: string
}

// A recorded protocol deviation.
export interface Deviation {
  id: number
  activity?: string
  severity: "minor" | "major"
  description: string
  guidance?: string
}

// public/oversight_state.json
export interface OversightState {
  subject: string
  visit: string
  updated_at: number
  overall: "on-track" | "attention" | "deviation"
  checks: OversightCheck[]
  deviations?: Deviation[]
}

// An EDC data query flagged in the monitoring report.
export interface Query {
  form: string
  field: string
  query: string
}

// public/monitoring_report.json
export interface MonitoringReport {
  study: string
  subject: string
  visit: string
  generated_at: number
  summary?: string
  sdv?: {
    fields_total: number
    auto_sourced: number
    confirmed: number
    pending: number
  }
  protocol_compliance?: {
    required: number
    completed: number
    missed: string[]
  }
  deviations?: Deviation[]
  queries?: Query[]
  signoff_ready?: boolean
  recommendation?: string
}

// After-visit summary (see visit_summary.py -> public/visit_summary.json).
export interface VisitSummary {
  patient: string
  generated_at: number
  duration_s: number
  summary: string
  topics_covered: string[]
  topics_missed: string[]
  vitals: {
    hr_avg: number | null
    fatigue_peak: number | null
    final_recommendation: string | null
  }
  emotional_arc: string
}
