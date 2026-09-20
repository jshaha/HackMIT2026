import type {
  Reading,
  AgendaState,
  VisitSummary,
  SoaState,
  EdcForms,
  OversightState,
  MonitoringReport,
} from "./types"

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
    speech_s: 9.7,
    speaker_similarity: 0.94,
    is_patient: true,
  },
  face_emotion: {
    arousal: 0.62,
    valence: 0.58,
    emotional_state: "engaged / calm",
    source: "face-video (hsemotion)",
  },
  fatigue: {
    too_fatigued: false,
    fatigue_score: 0.29,
    confidence: 0.78,
    next_action: "continue",
    trial_recommendation: "continue",
    reasoning:
      "Fatigue markers within acceptable range — patient can continue. Heart-rate and " +
      "breathing are steady and vocal energy is normal.",
    key_factors: [
      "HR 70.6 BPM (normal)",
      "BR 14.2/min (normal)",
      "voice fatigue_index 0.29",
      "voice arousal_index 0.58 (ok)",
    ],
    contributions: [
      { feature: "voice fatigue_index", value: 0.29, weight: 0.35, contribution: 0.10, note: "vocal fatigue low" },
      { feature: "voice arousal", value: 0.58, weight: 0.20, contribution: 0.08, note: "energy normal" },
      { feature: "heart rate", value: 70.6, weight: 0.25, contribution: 0.07, note: "within resting range" },
      { feature: "breathing rate", value: 14.2, weight: 0.10, contribution: 0.02, note: "regular" },
      { feature: "face arousal", value: 0.62, weight: 0.10, contribution: 0.02, note: "attentive" },
    ],
    weights_rationale:
      "Voice fatigue and arousal weighted highest as the strongest short-horizon fatigue signals; " +
      "heart rate anchors the physiological baseline.",
    engine: "rule-core",
    inputs: { heart_leg: true, voice_leg: true },
  },
  embedding: { n_segments: 3, dim: 512, mean: makeEmbedding(512) },
}

// Demo visit agenda — mixes covered, pending and a 'suggested' (unconfirmed) item
// so the confirm/dismiss UI is exercised in the demo view.
export const demoAgenda: AgendaState = {
  patient: "Alex Rivera",
  updated_at: Date.now() / 1000,
  elapsed_s: 412,
  covered: 2,
  total: 5,
  items: [
    {
      text: "Confirm current medications and dosages",
      covered: true,
      status: "covered",
      evidence: "still taking the 10mg every morning like we said",
      similarity: 0.91,
      covered_at: Date.now() / 1000 - 300,
    },
    {
      text: "Ask about sleep quality this past week",
      covered: true,
      status: "covered",
      evidence: "honestly I've been sleeping a lot better since the change",
      similarity: 0.88,
      covered_at: Date.now() / 1000 - 180,
    },
    {
      text: "Review any new side effects",
      covered: false,
      status: "suggested",
      evidence: "the mornings feel a little off, sort of foggy",
      similarity: 0.64,
      covered_at: null,
    },
    {
      text: "Discuss upcoming dose adjustment plan",
      covered: false,
      status: "pending",
      evidence: null,
      similarity: null,
      covered_at: null,
    },
    {
      text: "Schedule the follow-up appointment",
      covered: false,
      status: "pending",
      evidence: null,
      similarity: null,
      covered_at: null,
    },
  ],
  pending: ["Discuss upcoming dose adjustment plan", "Schedule the follow-up appointment"],
}

// Demo after-visit summary.
export const demoVisitSummary: VisitSummary = {
  patient: "Alex Rivera",
  generated_at: Date.now() / 1000,
  duration_s: 428,
  summary:
    "Patient reported improved sleep and good medication adherence over the past week. Some mild " +
    "morning grogginess was noted and should be monitored. Vitals remained stable throughout the " +
    "visit and the patient stayed engaged. No urgent concerns; a dose-adjustment discussion and " +
    "follow-up scheduling were left for next contact.",
  topics_covered: [
    "Current medications and dosages",
    "Sleep quality this past week",
    "General mood and engagement",
  ],
  topics_missed: ["Upcoming dose adjustment plan", "Follow-up appointment scheduling"],
  vitals: {
    hr_avg: 70.6,
    fatigue_peak: 0.34,
    final_recommendation: "Continue — patient tolerated the visit well.",
  },
  emotional_arc:
    "Started slightly guarded, warmed up quickly, and stayed calm and engaged through the close.",
}

// ── Clinical-trial (Veeva/EDC) demo samples — coherent for S-001 / V2 ──────
// Study REGN-DEMO-2026, subject S-001, visit V2 (Baseline / Day 1).
// Exercises every panel state without being alarming: VS auto-filled as DRAFT,
// one suggested + one pending SoA activity, one 'warn' oversight check, and a
// monitoring report with a single minor deviation.
const NOW_ISO = new Date().toISOString()

// Demo Schedule of Activities — one done, one suggested, one pending.
export const demoSoa: SoaState = {
  study: "REGN-DEMO-2026",
  subject: "S-001",
  visit: "V2",
  in_window: true,
  updated_at: Date.now() / 1000,
  required_total: 4,
  done: 2,
  activities: [
    {
      id: "vital_signs",
      name: "Vital signs",
      form: "VS",
      required: true,
      status: "done",
      evidence: "HR 70.6 captured (draft)",
      source: "edc",
    },
    {
      id: "conmeds",
      name: "Concomitant medications review",
      form: "CM",
      required: true,
      status: "done",
      evidence: "10mg every morning noted",
      source: "edc",
    },
    {
      id: "ae_review",
      name: "Adverse event review",
      form: "AE",
      required: true,
      status: "suggested",
      evidence: "patient mentioned morning grogginess",
      source: "transcript",
    },
    {
      id: "fatigue_pro",
      name: "Fatigue PRO assessment",
      form: "PRO",
      required: true,
      status: "pending",
      evidence: null,
      source: null,
    },
  ],
  pending: ["fatigue_pro"],
}

// Demo eCRF forms — VS auto-filled as DRAFT with rPPG provenance (incl. a
// vital-signs draft field), one confirmed CM form, empty required AE fields.
export const demoEdcForms: EdcForms = {
  study: "REGN-DEMO-2026",
  subject: "S-001",
  visit: "V2",
  updated_at: Date.now() / 1000,
  forms: [
    {
      form: "VS",
      name: "Vital Signs",
      subject: "S-001",
      visit: "V2",
      status: "draft",
      fields: [
        {
          id: "hr",
          label: "Heart rate (bpm)",
          type: "number",
          value: 70.6,
          status: "draft",
          required: true,
          source: "contactless-rppg",
          captured_at: NOW_ISO,
          confidence: 0.72,
        },
        {
          id: "resp_rate",
          label: "Respiratory rate (/min)",
          type: "number",
          value: 14.2,
          status: "draft",
          required: true,
          source: "contactless-rppg",
          captured_at: NOW_ISO,
          confidence: 0.72,
        },
        {
          id: "bp_sys",
          label: "Blood pressure — systolic (mmHg)",
          type: "number",
          value: null,
          status: "empty",
          required: true,
          source: null,
        },
        {
          id: "bp_dia",
          label: "Blood pressure — diastolic (mmHg)",
          type: "number",
          value: null,
          status: "empty",
          required: true,
          source: null,
        },
        {
          id: "vs_method",
          label: "Method",
          type: "text",
          value: "Contactless rPPG (camera)",
          status: "draft",
          source: "contactless-rppg",
          captured_at: NOW_ISO,
          confidence: 0.72,
        },
        {
          id: "vs_datetime",
          label: "Measured at",
          type: "datetime",
          value: NOW_ISO,
          status: "draft",
          source: "contactless-rppg",
          captured_at: NOW_ISO,
          confidence: 0.72,
        },
      ],
    },
    {
      form: "PRO",
      name: "Fatigue PRO",
      subject: "S-001",
      visit: "V2",
      status: "draft",
      fields: [
        {
          id: "fatigue_score",
          label: "Fatigue score (0–1)",
          type: "number",
          value: 0.29,
          status: "draft",
          required: true,
          source: "fatigue-model",
          captured_at: NOW_ISO,
          confidence: 0.78,
        },
        {
          id: "too_fatigued",
          label: "Too fatigued to continue?",
          type: "bool",
          value: false,
          status: "draft",
          required: true,
          source: "fatigue-model",
          captured_at: NOW_ISO,
          confidence: 0.78,
        },
        {
          id: "pro_notes",
          label: "Notes",
          type: "text",
          value: null,
          status: "empty",
          source: null,
        },
      ],
    },
    {
      form: "CM",
      name: "Concomitant Medications",
      subject: "S-001",
      visit: "V2",
      status: "confirmed",
      fields: [
        {
          id: "cm_name",
          label: "Medication",
          type: "text",
          value: "Atorvastatin",
          status: "confirmed",
          required: true,
          source: "transcript-extraction",
          captured_at: NOW_ISO,
          confidence: 0.61,
          signer: "clinician",
          signed_at: NOW_ISO,
        },
        {
          id: "cm_dose",
          label: "Dose",
          type: "text",
          value: "10 mg once daily",
          status: "confirmed",
          required: true,
          source: "transcript-extraction",
          captured_at: NOW_ISO,
          confidence: 0.61,
          signer: "clinician",
          signed_at: NOW_ISO,
        },
      ],
    },
    {
      form: "AE",
      name: "Adverse Events",
      subject: "S-001",
      visit: "V2",
      status: "empty",
      fields: [
        {
          id: "ae_term",
          label: "Adverse event term",
          type: "text",
          value: null,
          status: "empty",
          required: true,
          source: null,
        },
        {
          id: "ae_severity",
          label: "Severity",
          type: "enum",
          value: null,
          status: "empty",
          required: true,
          source: null,
        },
        {
          id: "ae_onset",
          label: "Onset",
          type: "datetime",
          value: null,
          status: "empty",
          required: true,
          source: null,
        },
      ],
    },
  ],
}

// Demo live oversight — on-track overall, with one 'warn' (symptom mentioned
// but AE form still empty) and one minor deviation.
export const demoOversight: OversightState = {
  subject: "S-001",
  visit: "V2",
  updated_at: Date.now() / 1000,
  overall: "attention",
  checks: [
    {
      activity: "vital_signs",
      status: "pass",
      detail: "Vital Signs captured (draft) and within window.",
      guidance: "Confirm and e-sign the draft VS values.",
    },
    {
      activity: "conmeds",
      status: "pass",
      detail: "Concomitant medications reviewed and confirmed.",
    },
    {
      activity: "ae_review",
      status: "warn",
      detail: "Patient mentioned 'morning grogginess' but the AE form is empty.",
      guidance: "Open the Adverse Events form and record the reported symptom.",
    },
    {
      activity: "fatigue_pro",
      status: "pass",
      detail: "Fatigue PRO auto-drafted from the fatigue model.",
      guidance: "Confirm and e-sign the PRO draft to complete this activity.",
    },
  ],
  deviations: [
    {
      id: 1,
      activity: "fatigue_pro",
      severity: "minor",
      description: "Fatigue PRO not yet e-signed at time of capture.",
      guidance: "Clinician to confirm the PRO draft before visit close.",
    },
  ],
}

// Demo post-visit monitoring report — not sign-off-ready due to pending drafts.
export const demoMonitoringReport: MonitoringReport = {
  study: "REGN-DEMO-2026",
  subject: "S-001",
  visit: "V2",
  generated_at: Date.now() / 1000,
  summary:
    "Vital signs and Fatigue PRO were auto-sourced from the contactless capture and remain " +
    "unconfirmed drafts. Concomitant medications were reviewed and e-signed. A reported symptom " +
    "(morning grogginess) has not yet been recorded on the Adverse Events form.",
  sdv: { fields_total: 14, auto_sourced: 8, confirmed: 2, pending: 6 },
  protocol_compliance: { required: 4, completed: 2, missed: ["fatigue_pro"] },
  deviations: [
    {
      id: 1,
      activity: "fatigue_pro",
      severity: "minor",
      description: "Fatigue PRO not yet e-signed at time of capture.",
      guidance: "Clinician to confirm the PRO draft before visit close.",
    },
  ],
  queries: [
    { form: "AE", field: "ae_term", query: "Reported symptom not entered on AE form." },
    { form: "VS", field: "bp_sys", query: "Blood pressure not captured (manual entry required)." },
  ],
  signoff_ready: false,
  recommendation: "Confirm draft VS/PRO fields and record the reported symptom before sign-off.",
}
