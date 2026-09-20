# DB Handoff — vitals persistence

SQLite persistence for patient fatigue readings. Stdlib only (`sqlite3`), no new deps.
DB file is git-ignored (patient health data). Single module: **`db.py`**.

## Entities
- **patients** — opaque `patient_code` (avoid real names / PII), optional alias/dob/age/sex/notes.
- **readings** — one capture event, belongs to a patient. Indexed by `(patient_id, captured_at)`.
- **heart_features** — heart leg (from `reading.json`): hr, sqi, fs, duration_s, hrv.* flattened, `bvp_json`, `embedding_json`. Nullable (may be absent).
- **voice_features** — voice leg (from `voice_features.json`): all 14 acoustic markers. Nullable.
- **fatigue_decisions** — output of the agentic loop (one per reading): `too_fatigued`, `confidence`, `reasoning`, `model`, `features_json` snapshot.
- **doctor_notes** — longitudinal free-text clinician context (many per patient): `note`, `author`, `category` (history/medication/instruction), `note_date`. Gives the agentic loop *context awareness* about the patient beyond vitals.

One patient → many readings. A reading may have a heart leg, a voice leg, or both — each is its own row keyed by `reading_id`, so either can be null. Full schema is `SCHEMA_SQL` in `db.py` (authoritative).

## One-line ingest of existing JSON
```bash
python db.py ingest --patient P001 --reading reading.json --voice voice_features.json --decision decision.json
python db.py list   --patient P001
python db.py init            # create tables (also auto-created on first write)
```
`ingest` gets-or-creates the patient by code, then makes one reading with whichever
legs you pass (`--reading`/`--voice`; at least one required) plus, optionally, the
fatigue-loop output (`--decision decision.json`). `decision.json`'s `engine` maps to
`model`; the full decision dict is kept as the fatigue `features` snapshot so the
extra fields (`fatigue_score`, `next_action`, `key_factors`, `inputs`) survive.

**`run_pipeline.sh` auto-persists** the final decision after every run — set the
patient with `PATIENT=P002 ./run_pipeline.sh`. Heart leg is attached only when
`reading.json` exists (i.e. the video leg ran in the pipeline conda env).

## Doctor's notes / patient context
Give the agentic loop context about the patient (history, meds, instructions) so
it doesn't reason from vitals alone.
```bash
python db.py note    --patient P001 --text "Chronic fatigue Hx; on beta-blockers." \
                     --author Dr.Lee --category history --date 2026-08-01
python db.py note    --patient P001 --file consult.txt          # note body from a file
python db.py ingest  --patient P001 --voice voice_features.json --note "Drowsy at capture."
python db.py context --patient P001                             # demographics + notes + recent readings
```
```python
db.insert_doctor_note(pid, "Post-op week 2; sleeps poorly.", author="Dr.Lee", category="history")
notes = db.get_doctor_notes(pid)                 # oldest->newest
ctx   = db.get_patient_context("P001")           # {patient, doctor_notes, recent_readings}
prompt_block = db.format_patient_context("P001") # ready-to-paste text for the LLM prompt
```
`get_patient_context` / `format_patient_context` accept a `patient_code` (str) or
id (int). The agentic loop should call `format_patient_context(code)` and prepend
the returned block to its prompt before deciding `too_fatigued`.

## Per-patient baselines (personal z-scoring)
Fatigue is within-subject, so `fatigue_agent.py --patient CODE` scores each feature
against the patient's own baseline (mean/std) instead of absolute population
cutoffs. Purely additive: with no baseline the agent behaves exactly as before.
```bash
python db.py ingest   --patient P001 --voice rested.json --baseline   # tag a rested capture
python db.py baseline --patient P001                                   # inspect mean/std/n
python fatigue_agent.py --reading reading.json --voice voice_features.json --patient P001
```
```python
bl = db.get_baseline(pid)   # {"n_readings", "source", "features": {col: {mean,std,n}}}
```
- **Baseline set:** readings tagged `is_baseline=1` if any exist; else the patient's
  prior non-fatigued readings (`too_fatigued` not = 1). Needs **≥3 samples/feature**
  (`min_n`) with non-zero std, else that feature uses the absolute cutoff.
- **Features baselined:** `hr, hrv_rmssd, hrv_sdnn, hrv_breathingrate` (heart) and
  `fatigue_index, arousal_index, pause_ratio, speech_rate_hz, shimmer_pct` (voice).
- **Schema:** `readings.is_baseline` (added by an auto-migration in `init_db` for
  older DBs — no manual step). `decision.json` now carries a `baseline` summary block.
- **Caveat:** with few, tightly-clustered baseline captures the std is tiny, so small
  deviations saturate. Prefer 5+ baseline captures spanning normal daily variation.

## Library API (for export_reading.py / voice_decoder.py)
```python
import db
pid = db.insert_patient("P001")                     # idempotent get-or-create
rid = db.insert_reading(pid, reading=reading_dict)   # heart leg; captured_at read from dict
db.add_voice_features(rid, voice_dict)               # attach voice leg to same reading
db.insert_fatigue_decision(rid, {                    # from the agentic loop
    "too_fatigued": True, "confidence": 0.82,
    "reasoning": "...", "model": "claude-opus-4-8",
    "features": {"hr": 93.6, "fatigue_index": 0.305},
})
rows = db.get_readings(pid)   # oldest->newest; each reassembled to nested dicts
                              # {captured_at, source, reading{...}, voice{...}, fatigue{...}}
```
The helpers ingest the **exact dicts** the capture scripts already build — pass what you'd otherwise `json.dump`. Every function takes an optional `db_path=` (or set env `VITALS_DB`); default `vitals.db`.

## Notes
- Timestamps ISO8601 UTC. Voice JSON has no timestamp of its own → `insert_reading` falls back to the heart leg's `captured_at`, else `now()`.
- Waveforms/embeddings stored as JSON text (fine at this scale; no normalized points table).
- To auto-persist from a capture script, add `import db; db.insert_reading(db.insert_patient(code), reading=reading)` right after it builds the dict.
