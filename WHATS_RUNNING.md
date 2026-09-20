# What's running (live)

**Contactless clinical-trial vitals + fatigue monitor — no wearable, no blood draw.**
The iPhone's camera and mic are the only sensors.

- **Vitals from a face video** — remote photoplethysmography (open-rppg) recovers
  **heart rate, breathing rate, HRV and signal quality** from tiny color changes in
  the face, at 30 fps.
- **Emotional state from the face** — a lightweight facial-expression model (FER+)
  reads **arousal / valence** and an emotion label.
- **Voice analysis** — the patient's voice (speaker-verified, so only *them*) runs
  through a wav2vec2 speech-emotion model + acoustic markers for **vocal fatigue**.
- **Agentic fatigue loop** — all three modalities are fused with **research-weighted
  coefficients** into one fatigue score; an LLM then classifies whether the patient is
  **too fatigued to continue the trial → continue / pause / stop** (with a live alert).
- **Veeva EDC integration** — vitals + the conversation auto-fill and **e-sign the
  eCRF** (Vital Signs, Adverse Events, Concomitant Meds, Fatigue PRO), shaped like
  Veeva Vault CDMS.
- **Automated clinical oversight** — checks each protocol Schedule-of-Activities step,
  flags deviations, and **tells the clinician what to do / discuss next** (e.g. "a
  symptom was mentioned but not recorded").
- **Two views, one pipeline** — the **iPhone shows it as an AR overlay** floating
  around the patient's face; the **web dashboard** mirrors everything live.

Everything runs **locally on the Mac** (the iPhone streams over USB) — Apple-GPU
accelerated for the voice model, CPU for the tiny rPPG nets. Not medical grade.
