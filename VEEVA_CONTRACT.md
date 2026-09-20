# Veeva-integration contract (mock Vault CDMS + EDC auto-fill + oversight)

Shared spec for the clinical-trial layer. Every agent builds to THIS. Goal: show our
contactless-vitals + conversation system integrates with a trial's **Schedule of
Activities (SoA)** and **EDC/eCRF** (Veeva Vault CDMS-shaped), (1) suggests required
activities not yet covered, (2) **auto-fills the eCRF as DRAFTS the clinician confirms**
(ALCOA-C: attributable, with provenance), and (3) provides **automated oversight** —
live protocol-compliance guidance + a post-visit monitoring report — so no separate
monitor is needed.

## Environments
- `papagei_env` (py3.10; torch/transformers/soundfile/numpy; `.env` has `OPENAI_API_KEY`). Run: `conda run -n papagei_env python ...`. AVOID onnxruntime/webrtcvad (segfault here).
- Data flows through JSON files the dashboard polls in `vitals-dashboard/public/`.
- Existing inputs available: `vitals-dashboard/public/reading.json` (hr, br, sqi, hrv, voice{...}, face_emotion{...}, fatigue{fatigue_score,too_fatigued,trial_recommendation,...}); `decision.json`; `vitals-dashboard/public/agenda_state.json`.

## Mock data model (Vault CDMS-shaped)
Committed JSON DEFINITIONS live under `veeva/`:
- `veeva/protocol_soa.json` — study + visits + activities (the SoA).
- `veeva/crf_forms.json` — eCRF form definitions (fields).
- `veeva/subjects.json` — enrolled subjects.
Mutable EDC state lives in `veeva/edc_store.json` (do NOT commit — runtime).

### veeva/protocol_soa.json
```json
{
  "study": {"id":"REGN-DEMO-2026","name":"Contactless Vitals Sub-study (DEMO/MOCK)","protocol_version":"1.0","sponsor":"Regeneron (demo)"},
  "visits": [
    {"id":"SCR","name":"Screening","day":-14,"window_days":[-21,-1]},
    {"id":"V2","name":"Baseline / Day 1","day":1,"window_days":[0,3]},
    {"id":"V3","name":"Week 4","day":28,"window_days":[25,31]}
  ],
  "activities": [
    {"id":"informed_consent","name":"Informed consent","form":"IC","visits":["SCR"],"required":true},
    {"id":"vital_signs","name":"Vital signs","form":"VS","visits":["SCR","V2","V3"],"required":true},
    {"id":"ae_review","name":"Adverse event review","form":"AE","visits":["V2","V3"],"required":true},
    {"id":"conmeds","name":"Concomitant medications review","form":"CM","visits":["SCR","V2","V3"],"required":true},
    {"id":"fatigue_pro","name":"Fatigue PRO assessment","form":"PRO","visits":["V2","V3"],"required":true},
    {"id":"phys_exam","name":"Physical examination","form":"PE","visits":["SCR","V3"],"required":false}
  ]
}
```
Demo "current" context: study REGN-DEMO-2026, subject `S-001`, visit `V2`.

### veeva/crf_forms.json  (each field: id,label,type,autofill(bool),autofill_source)
autofill_source ∈ {"rppg","fatigue","transcript",null}. Include at least:
- `VS` Vital Signs: `hr`(number, rppg), `resp_rate`(number, rppg), `bp_sys`/`bp_dia`(number, manual/null), `vs_method`(text, rppg="Contactless rPPG (camera)"), `vs_datetime`(datetime, rppg).
- `AE` Adverse Events: `ae_term`(text, transcript), `ae_severity`(enum mild|moderate|severe, transcript), `ae_related`(enum unrelated|possible|probable, manual), `ae_onset`(datetime, transcript).
- `CM` Concomitant Medications: `cm_name`(text, transcript), `cm_dose`(text, transcript), `cm_indication`(text, transcript).
- `PRO` Fatigue PRO: `fatigue_score`(number 0-1, fatigue), `too_fatigued`(bool, fatigue), `pro_notes`(text, transcript).
- `IC` Informed Consent: `consent_signed`(bool, manual), `consent_date`(datetime, manual).
- `PE` Physical Exam: `pe_normal`(bool, manual), `pe_findings`(text, transcript).

## veeva_mock.py — mock Vault CDMS client (Agent 1 owns)
A class `VaultCDMS(root="veeva")` mirroring Vault REST semantics (study→casebook→event/visit→form→item), backed by the JSON above. Methods (stable API — others depend on these):
- `get_study() -> dict`
- `list_visits() -> list[visit]`
- `get_soa(visit_id=None) -> list[activity]`  (activities required at that visit; all if None)
- `get_form_def(form_id) -> dict`
- `get_forms(subject, visit) -> list[form_instance]`  (see form_instance below; creates empty instances from defs on first call)
- `get_form(subject, visit, form_id) -> form_instance`
- `submit_form_data(subject, visit, form_id, fields: dict[field_id->value], status="draft", source=None, confidence=None) -> form_instance`  (upserts field values with provenance; field.status="draft")
- `confirm_form(subject, visit, form_id, signer="clinician") -> form_instance`  (sets each draft field.status="confirmed", stamps signer+time; ALCOA e-sign)
- `record_deviation(subject, visit, activity_id, severity, description) -> deviation`
- `list_deviations(subject, visit) -> list[deviation]`
- `visit_in_window(visit_id, day_offset) -> bool`  (day_offset from Day 1; default assume in-window for demo)
All persist to `veeva/edc_store.json`. Never raise on missing subject/visit — create lazily.

### form_instance shape (returned by get_forms / used everywhere)
```json
{"form":"VS","name":"Vital Signs","subject":"S-001","visit":"V2",
 "status":"empty|draft|confirmed",
 "fields":[
   {"id":"hr","label":"Heart rate (bpm)","type":"number","value":72,
    "status":"empty|draft|confirmed","source":"contactless-rppg","captured_at":"<iso>","confidence":0.9,
    "signer":null,"signed_at":null}
 ]}
```
Field `status`: empty (no value) → draft (auto-filled, unconfirmed) → confirmed (clinician e-signed). `source`/`captured_at`/`confidence` = provenance (ALCOA attributability).

## edc_autofill.py  (Agent 1 owns)
Reads `reading.json`/`decision.json` (vitals+fatigue) and `transcript.json` (conversation), maps to CRF fields, and calls `veeva_mock.submit_form_data(..., status="draft", source=...)`. Publishes the whole EDC picture for the dashboard.
- VS ← reading: hr←reading.hr, resp_rate←reading.br, vs_method←"Contactless rPPG (camera)", vs_datetime←captured_at (source "contactless-rppg", confidence←sqi).
- PRO ← fatigue: fatigue_score←fatigue.fatigue_score, too_fatigued←fatigue.too_fatigued (source "fatigue-model").
- AE/CM ← transcript: use an LLM (OpenAI) to EXTRACT adverse events + medications mentioned (return JSON list); write as DRAFT with low confidence + source "transcript-extraction" (flag for clinician review). Deterministic keyword fallback if no key. NEVER auto-confirm; everything stays "draft".
- **Publishes `vitals-dashboard/public/edc_forms.json`**:
```json
{"study":"REGN-DEMO-2026","subject":"S-001","visit":"V2","updated_at":<epoch>,
 "forms":[ <form_instance>, ... ]}
```
CLI: `python edc_autofill.py --subject S-001 --visit V2` (one pass) — designed to be called on a loop by the orchestrator.

## oversight.py  (Agent 2 owns)
The automated CRA/monitor. Consumes veeva_mock (SoA + forms + deviations), `reading.json`, `agenda_state.json`, `transcript.json`. Two outputs:

**A) Per-activity SoA status → `vitals-dashboard/public/soa_state.json`** (live):
```json
{"study":"REGN-DEMO-2026","subject":"S-001","visit":"V2","in_window":true,"updated_at":<epoch>,
 "required_total":4,"done":2,
 "activities":[{"id":"vital_signs","name":"Vital signs","form":"VS","required":true,
                "status":"done|pending|suggested","evidence":"HR 72 captured (draft)","source":"edc|transcript"}],
 "pending":["ae_review","fatigue_pro"]}
```
activity status: done (form has data / confirmed), suggested (transcript/agenda implies addressed but no EDC data yet), pending (nothing).

**B) Live oversight + deviations → `vitals-dashboard/public/oversight_state.json`**:
```json
{"subject":"S-001","visit":"V2","updated_at":<epoch>,"overall":"on-track|attention|deviation",
 "checks":[{"activity":"ae_review","status":"pass|warn|fail",
            "detail":"Patient mentioned 'headache' but AE form is empty",
            "guidance":"Open the Adverse Events form and record the reported headache."}],
 "deviations":[{"id":1,"activity":"fatigue_pro","severity":"minor|major",
                "description":"Required Fatigue PRO not captured","guidance":"..."}]}
```
Checks to implement: (1) each required SoA activity — is EDC data present? in window? (2) safety: if `reading.fatigue.too_fatigued` → a check advising pause per protocol; (3) transcript-vs-EDC gap: a symptom/med mentioned in transcript but no corresponding AE/CM draft → warn + guidance. Record real deviations via `veeva_mock.record_deviation`.

**C) Post-visit report → `vitals-dashboard/public/monitoring_report.json`** (CLI `--report`):
```json
{"study":..,"subject":..,"visit":..,"generated_at":<epoch>,"summary":"<narrative>",
 "sdv":{"fields_total":N,"auto_sourced":N,"confirmed":N,"pending":N},
 "protocol_compliance":{"required":4,"completed":3,"missed":["fatigue_pro"]},
 "deviations":[...],"queries":[{"form":"AE","field":"ae_severity","query":"Severity not graded"}],
 "signoff_ready":false,"recommendation":"<one line>"}
```
CLI: `python oversight.py --subject S-001 --visit V2` (writes soa_state + oversight_state) and `--report` (also writes monitoring_report).

## conversation_tracker.py + orchestration  (Agent 3 owns)
- Add a **transcript export**: write `vitals-dashboard/public/transcript.json` = `{"patient":..,"subject":"S-001","updated_at":<epoch>,"elapsed_s":..,"text":"<full running transcript>","segments":[{"t":..,"text":..}]}` each round (edc_autofill + oversight read this). Keep existing agenda behavior.
- Add an OPTION to source agenda topics from the **SoA** for the visit (via veeva_mock.get_soa) in addition to db notes — a `--soa-visit V2` flag mapping required activities to agenda topics.
- Create `visit_pipeline.py`: a small loop orchestrator that every ~8–10s runs `edc_autofill` then `oversight` (import and call their entrypoints, or subprocess) so the EDC + oversight JSONs stay live. CLI `python visit_pipeline.py --subject S-001 --visit V2`.
- Add a `--veeva` flag to `monitor.sh` that also launches `visit_pipeline.py` (papagei_env) alongside the monitors (same pattern as the existing `--agenda` flag; add a `start_veeva` fn, pkill in stop(), a log line in status()).

## Dashboard  (Agent 4 owns — vitals-dashboard/src/** only)
Poll these new files in the live effect and add panels (match existing style: Tailwind v4 tokens, motion/react, `live` prop, existing panels):
- **SoA panel** (`soa_state.json`): required activities with done/suggested/pending, done/total, in-window badge.
- **eCRF panel** (`edc_forms.json`): the forms with their fields; DRAFT auto-filled fields highlighted (amber) with their provenance (source + confidence); a **Confirm / e-sign** button per form (or per field) that marks fields confirmed in LOCAL state (persist across polls, like the agenda confirm). Show empty required fields.
- **Oversight panel** (`oversight_state.json`): overall status badge, the checks (pass/warn/fail with guidance), and deviations. Make warn/fail visually prominent.
- **Monitoring report** (`monitoring_report.json`): SDV summary, compliance, deviations, queries, sign-off-ready + recommendation.
Add all new TS types; update demoData.ts with coherent samples so the demo view shows every panel. Must pass `npx tsc -b --noEmit` and `npx oxlint src`.

## Global rules for all agents
- Edit ONLY your owned files. Do NOT run git. Do NOT edit `.gitignore` — instead report new runtime outputs to ignore (I'll handle it). Do NOT destabilize the env (no onnxruntime; don't change torch/transformers pins). Do NOT run live loops that block (mic/camera) — unit-test with scripted/synthetic data. Everything degrades gracefully (missing files/keys → sensible defaults, never crash). Keep everything ALCOA-C: auto-filled data is always DRAFT with provenance; only the clinician confirm step marks it confirmed.
```
