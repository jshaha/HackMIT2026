"""
Agentic fatigue-scoring engine — the decision node of the pipeline.

Fuses EVERY available contactless fatigue marker into a single fatigue_score in
[0,1], then lets an LLM classify whether the patient is "too fatigued to continue
the clinical trial", grounded in that score + a transparent contribution
breakdown + the patient's chart.

Three modalities are consolidated (spec: rPPG heart, voice, video facial affect):

  - HEART / rPPG  (reading.json):
      HRV RMSSD  (low = fatigue; strongest autonomic marker of fatigue)
      HR         (weak, non-specific; deviation only)
      breathing  (weak, non-specific; deviation only)
      SQI        (QUALITY GATE, not a fatigue term)
  - VOICE  (reading["voice"] OR voice_features.json):
      fatigue_index  (composite vocal fatigue score)
      arousal_index  (low = fatigue)
      pause_ratio    (high = fatigue), speech_rate_hz (low = fatigue)
      shimmer_pct    (high = vocal instability/strain)
  - VIDEO FACIAL AFFECT  (reading["face_emotion"], may be absent/null):
      arousal (low = fatigue), valence (negative = fatigue),
      emotional_state ("sad"/"tired"/"fatigued" raise fatigue)

Design (matches the whiteboard "agentic loop to classify fatigue"):
  1. Deterministic RULE CORE fuses grounded numbers into fatigue_score [0,1] with
     RESEARCH-BACKED weights (see WEIGHTS below) and a deterministic
     trial_recommendation / next_action. ALWAYS runs — no network, no API key,
     fully reproducible. This is both the grounding and the LLM fallback.
  2. Optional AGENTIC LLM layer: if an LLM key is set, the model sees the score,
     the per-feature contributions, and the patient chart (db.get_patient_context)
     and returns {too_fatigued, trial_recommendation, next_action, confidence,
     reasoning}. It is grounded in the deterministic result, not asked to invent
     numbers, and the deterministic call remains the fallback on any error.

Quality/coverage loop: SQI < MIN_SQI or no usable feature -> next_action
"recheck", too_fatigued=null (inconclusive; re-capture) rather than a
false-confident verdict.

Per-patient baselines: pass --patient to score each feature as a personal
deviation (z-score vs the patient's rested readings in the DB) instead of an
absolute population cutoff — fatigue is a within-subject change, so this is the
main accuracy lever. Falls back to absolute cutoffs when no baseline exists.

Run in any env with stdlib (numpy optional; db.py optional):
    python fatigue_agent.py \
        --reading vitals-dashboard/public/reading.json \
        --voice voice_features.json \
        --patient P001 \
        --out decision.json
"""
import argparse
import json
import os
import urllib.request

# ---------------------------------------------------------------------------
# RESEARCH-BACKED FUSION WEIGHTS
# ---------------------------------------------------------------------------
# Fatigue is multimodal; the literature is consistent that the three modalities
# differ in predictive strength. We weight them accordingly (each weight is the
# feature's share of the fused score BEFORE renormalization over the features
# actually present; renormalization happens in fuse()). Every weight carries a
# one-line rationale + source. Numbers are chosen to reflect *relative* evidence
# strength, not fit to any single dataset.
#
# Modality-level evidence:
#   - Facial behavioural cues (PERCLOS / facial affect) are the validated
#     "gold-standard" observable fatigue signal; multimodal studies report
#     ~92-94% accuracy with facial features carrying the largest single share.
#     (Nature Sci Rep 2025 s41598-025-86709-1; PMC13364414 emotion-aware fatigue;
#      Carnegie Mellon PERCLOS standard.)
#   - HRV RMSSD reduction is the strongest *physiological* (autonomic) fatigue
#     marker: it independently and negatively predicts fatigue across driving,
#     mental-fatigue and clinical (CFS/ME) populations.
#     (Frontiers Sports Act Living 2026 driver-fatigue HRV+ML;
#      PLOS One 2020 mental-fatigue HRV; J Transl Med 2019 CFS RMSSD.)
#   - Vocal arousal / fatigue cues (low arousal, slow rate, more pauses, higher
#     shimmer) are validated but noisier — "speaking rate and voice quality
#     features showed inconsistent results across studies" — so they get a
#     meaningful but smaller share. (Nature Sci Rep 2022 s41598-022-26375-9
#      acoustic stress; JVoice 2023 shimmer/jitter predictive value.)
#   - HR and breathing rate are non-specific (confounded by posture, emotion,
#     movement); they are low-weight deviation terms, not primary markers.
#   - SQI is a QUALITY GATE (decide()), never a fatigue term.
#
# 'dir' = fatigue direction for the ABSOLUTE fallback and for renormalized
# reporting: 'hi' fatigue rises with the value, 'lo' fatigue rises as it falls,
# 'abs' either extreme (used with personal baselines). Personal-baseline scoring
# overrides 'dir' per _z_contrib.
WEIGHTS = {
    # --- video facial affect (top-tier observable modality) -----------------
    "face_arousal": {
        "w": 0.22, "dir": "lo",
        "rationale": "Low facial arousal (flat/drowsy affect) is a core PERCLOS-"
                     "adjacent fatigue cue; facial modality carries the largest "
                     "single share in multimodal fatigue models (Nature SciRep "
                     "2025 s41598-025-86709-1; PMC13364414)."},
    "face_valence": {
        "w": 0.10, "dir": "lo",
        "rationale": "Negative facial valence (sad/tired affect) co-occurs with "
                     "fatigue; emotion-aware context improves fatigue accuracy "
                     "(PMC13364414 emotion-aware driver fatigue)."},
    # --- heart / rPPG autonomic ---------------------------------------------
    "hrv_rmssd": {
        "w": 0.24, "dir": "lo",
        "rationale": "Reduced RMSSD is the strongest physiological (vagal) fatigue "
                     "marker, independently & negatively predicting fatigue across "
                     "populations (Frontiers 2026 driver-fatigue HRV+ML; PLOS One "
                     "2020 mental fatigue; J Transl Med 2019 CFS)."},
    "hr": {
        "w": 0.05, "dir": "abs",
        "rationale": "HR is a weak, non-specific fatigue proxy (confounded by "
                     "emotion/movement); low-weight deviation term only "
                     "(Frontiers 2026 lists mean-HR as secondary to RMSSD)."},
    "breathing_rate": {
        "w": 0.04, "dir": "abs",
        "rationale": "Breathing rate is a weak, non-specific autonomic term; "
                     "small deviation weight (secondary autonomic index)."},
    # --- voice (validated but noisier) --------------------------------------
    "voice_fatigue_index": {
        "w": 0.15, "dir": "hi",
        "rationale": "Composite vocal fatigue score (low arousal + pauses + slow "
                     "rate + shimmer); vocal fatigue markers are validated but "
                     "less consistent than HRV/facial (Nature SciRep 2022; "
                     "JVoice 2023)."},
    "voice_arousal": {
        "w": 0.10, "dir": "lo",
        "rationale": "Low vocal arousal (reduced pitch energy/expressiveness) is a "
                     "recognised fatigue cue but noisier across studies (Nature "
                     "SciRep 2022 acoustic stress/arousal)."},
    "voice_pause_ratio": {
        "w": 0.05, "dir": "hi",
        "rationale": "More/longer pauses reflect reduced speech continuity under "
                     "fatigue (voice_decoder rationale; Nature SciRep 2022)."},
    "voice_speech_rate": {
        "w": 0.03, "dir": "lo",
        "rationale": "Speech slows with fatigue, though inconsistently across "
                     "studies -> small weight (Nature SciRep 2022)."},
    "voice_shimmer": {
        "w": 0.02, "dir": "hi",
        "rationale": "Higher shimmer indexes vocal-fold instability/strain; weak, "
                     "supporting cue (JVoice 2023 shimmer predictive value)."},
}

# One-line summary emitted in the decision object.
WEIGHTS_RATIONALE = (
    "Weights reflect the RELATIVE predictive strength of contactless fatigue "
    "markers in the literature: facial affect (PERCLOS-adjacent, ~0.32 total) "
    "and HRV RMSSD reduction (strongest autonomic marker, 0.24) dominate; vocal "
    "fatigue/arousal cues (0.35 total, validated but noisier) support; HR & "
    "breathing (0.09) are low-weight non-specific deviation terms; SQI is a "
    "quality gate, not a fatigue term. Sources: Nature SciRep 2025 "
    "s41598-025-86709-1 & PMC13364414 (facial/multimodal); Frontiers 2026 "
    "driver-fatigue HRV, PLOS One 2020, J Transl Med 2019 (HRV/RMSSD); Nature "
    "SciRep 2022 s41598-022-26375-9 & JVoice 2023 (vocal). Personal-baseline "
    "z-scoring is applied per feature when available; weights renormalize over "
    "features actually present."
)

THRESHOLD_PAUSE = 0.45    # fatigue_score >= this -> at least "pause" (recheck soon)
THRESHOLD_STOP = 0.60     # fatigue_score >= this -> "stop" (too fatigued / halt)
THRESHOLD = THRESHOLD_STOP  # back-compat alias (ar_bridge sends this to the phone UI)
MIN_SQI = 0.50            # below this the heart leg is untrustworthy


def _load_dotenv():
    """Populate os.environ from a sibling .env (gitignored) without overriding
    anything already set in the real environment."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _clip01(x):
    return float(min(1.0, max(0.0, x)))


Z_FULL = 2.0   # deviation (in std) at which a personal-baseline contribution saturates to 1.0


def _z_contrib(val, base, direction):
    """Map `val`'s deviation from the patient's personal baseline to a fatigue
    contribution in [0,1]. direction: 'hi' = fatigue when above baseline,
    'lo' = below, 'abs' = either extreme. None if the baseline std is unusable."""
    sd = base.get("std", 0.0)
    if sd <= 1e-6:
        return None
    z = (val - base["mean"]) / sd
    if direction == "lo":
        z = -z
    elif direction == "abs":
        z = abs(z)
    return _clip01(z / Z_FULL)


def load(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _load_baseline(patient_code, db_path):
    """Fetch the patient's personal baseline from the DB, or None. Never fatal —
    a missing DB / patient / import just means we fall back to absolute cutoffs."""
    if not patient_code:
        return None
    try:
        import db
        p = db.get_patient(patient_code, db_path=db_path)
        if not p:
            print(f"[agent] patient {patient_code} not in {db_path}; using absolute cutoffs.")
            return None
        bl = db.get_baseline(p["id"], db_path=db_path)
        if not bl.get("features"):
            print(f"[agent] no usable baseline for {patient_code} "
                  f"({bl.get('n_readings', 0)} reading(s)); using absolute cutoffs.")
            return None
        return bl
    except Exception as e:
        print(f"[agent] baseline unavailable ({e}); using absolute cutoffs.")
        return None


def _patient_context(patient_code, db_path):
    """Structured patient chart for the LLM (demographics + notes + recent
    readings), or None. Never fatal."""
    if not patient_code:
        return None
    try:
        import db
        return db.get_patient_context(patient_code, db_path=db_path)
    except Exception as e:
        print(f"[agent] patient context unavailable ({e}).")
        return None


def _baseline_summary(baseline, n_personal):
    """Compact, JSON-friendly note of how the baseline was applied."""
    if not baseline:
        return {"used": False, "features_scored": 0}
    return {
        "used": n_personal > 0,
        "features_scored": n_personal,
        "source": baseline.get("source"),
        "from_readings": baseline.get("n_readings"),
    }


# States (lowercased, substring match) that raise fatigue when reported by the
# facial-affect model, e.g. emotional_state="sad / low" or "tired".
_TIRED_STATES = ("tired", "fatigu", "drowsy", "exhaust", "sleepy", "sad", "low")


def _add(contribs, feature, value, personal_z, absolute_val, note, n_personal_ref):
    """Append a contribution using the personal-baseline z-score when available,
    else the absolute fatigue value. Returns whether a personal score was used.
    `contribs` rows store the RAW weighted value pre-renormalization in
    'raw'; the reported 'contribution' is filled in after renormalization."""
    spec = WEIGHTS[feature]
    if personal_z is not None:
        v = personal_z
        note = note + " [personal baseline z]"
        used_personal = True
    else:
        v = absolute_val
        used_personal = False
    contribs.append({
        "feature": feature,
        "value": round(float(value), 4) if value is not None else None,
        "weight": spec["w"],
        "_norm": _clip01(v),          # normalized fatigue value in [0,1]
        "note": note,
    })
    return used_personal


def fuse(reading, voice, baseline=None):
    """Consolidate every available marker into (score, contributions, sqi,
    n_personal).

    Returns a `contributions` list where each row is
    {feature, value, weight, _norm, note}; the caller renormalizes weights over
    the rows present and fills 'contribution' = renorm_weight * _norm.
    """
    bf = (baseline or {}).get("features", {})
    contribs = []
    n_personal = 0

    def bump(used):
        nonlocal n_personal
        if used:
            n_personal += 1

    sqi = None
    if reading:
        sqi = reading.get("sqi")
        hr = reading.get("hr")
        rmssd = (reading.get("hrv") or {}).get("rmssd")
        br = reading.get("br")
        if br is None:
            br = (reading.get("hrv") or {}).get("breathingrate")

        # HRV RMSSD — strongest physiological marker (low RMSSD -> fatigue).
        if rmssd is not None:
            z = _z_contrib(rmssd, bf["hrv_rmssd"], "lo") if "hrv_rmssd" in bf else None
            av = _clip01(1 - rmssd / 50.0)   # ~50 ms healthy; lower -> fatigue
            bump(_add(contribs, "hrv_rmssd", rmssd, z, av,
                      f"HRV RMSSD {rmssd} ms (low = fatigue)", None))

        # HR — weak deviation term (absolute only flags extremes).
        if hr is not None:
            z = _z_contrib(hr, bf["hr"], "abs") if "hr" in bf else None
            if hr < 55:
                av = _clip01((55 - hr) / 20.0)
            elif hr > 100:
                av = _clip01((hr - 100) / 40.0)
            else:
                av = 0.0
            bump(_add(contribs, "hr", hr, z, av,
                      f"HR {hr} BPM (non-specific deviation)", None))

        # Breathing rate — weak deviation term.
        if br is not None:
            z = _z_contrib(br, bf["hrv_breathingrate"], "abs") if "hrv_breathingrate" in bf else None
            av = _clip01(max(0.0, (10 - br) / 6.0, (br - 20) / 12.0))
            bump(_add(contribs, "breathing_rate", br, z, av,
                      f"breathing {br}/min (non-specific deviation)", None))

    if voice:
        fi = voice.get("fatigue_index")
        ar = voice.get("arousal_index")
        pause = voice.get("pause_ratio")
        rate = voice.get("speech_rate_hz")
        shim = voice.get("shimmer_pct")

        if fi is not None:
            z = _z_contrib(fi, bf["fatigue_index"], "hi") if "fatigue_index" in bf else None
            bump(_add(contribs, "voice_fatigue_index", fi, z, _clip01(fi),
                      f"vocal fatigue_index {fi}", None))
        if ar is not None:
            z = _z_contrib(ar, bf["arousal_index"], "lo") if "arousal_index" in bf else None
            bump(_add(contribs, "voice_arousal", ar, z, _clip01(1 - ar),
                      f"vocal arousal {ar} (low = fatigue)", None))
        if pause is not None:
            z = _z_contrib(pause, bf["pause_ratio"], "hi") if "pause_ratio" in bf else None
            bump(_add(contribs, "voice_pause_ratio", pause, z, _clip01(pause / 0.5),
                      f"pause_ratio {pause} (high = fatigue)", None))
        if rate is not None:
            z = _z_contrib(rate, bf["speech_rate_hz"], "lo") if "speech_rate_hz" in bf else None
            bump(_add(contribs, "voice_speech_rate", rate, z, _clip01(1 - rate / 4.5),
                      f"speech rate {rate} syl/s (low = fatigue)", None))
        if shim is not None:
            z = _z_contrib(shim, bf["shimmer_pct"], "hi") if "shimmer_pct" in bf else None
            bump(_add(contribs, "voice_shimmer", shim, z, _clip01(shim / 12.0),
                      f"shimmer {shim}% (high = vocal strain)", None))

    # Video facial affect — NEW, optional. reading["face_emotion"] =
    # {arousal, valence, emotional_state, source}. Handle absent/null gracefully.
    face = (reading or {}).get("face_emotion")
    if isinstance(face, dict):
        fa = face.get("arousal")
        fv = face.get("valence")
        state = (face.get("emotional_state") or "").lower()
        if fa is not None:
            av = _clip01(1 - float(fa))
            # a "tired"/"sad" state label nudges facial-arousal fatigue up
            if any(s in state for s in _TIRED_STATES):
                av = _clip01(max(av, 0.6))
            note = f"facial arousal {fa} (low = fatigue"
            note += f"; state '{state}')" if state else ")"
            _add(contribs, "face_arousal", fa, None, av, note, None)
        if fv is not None:
            av = _clip01(1 - float(fv))   # low/negative valence -> fatigue
            _add(contribs, "face_valence", fv, None, av,
                 f"facial valence {fv} (negative = fatigue)", None)

    if not contribs:
        return None, [], sqi, 0

    # Renormalize weights over the features actually present, then compute the
    # transparent contribution = renorm_weight * normalized_value.
    total_w = sum(c["weight"] for c in contribs)
    score = 0.0
    for c in contribs:
        rw = c["weight"] / total_w if total_w > 0 else 0.0
        c["contribution"] = round(rw * c["_norm"], 4)
        score += c["contribution"]
        c.pop("_norm", None)   # internal only
    return _clip01(score), contribs, sqi, n_personal


def _deterministic_recommendation(score):
    """Map a fatigue_score to the grounding/fallback trial recommendation."""
    if score >= THRESHOLD_STOP:
        return "stop", "halt", True
    if score >= THRESHOLD_PAUSE:
        return "pause", "recheck", False
    return "continue", "continue", False


def decide(score, contribs, sqi, have_reading, have_voice, have_face):
    """Deterministic verdict. Emits the full output contract minus the LLM/engine
    fields (added by the caller). SQI<MIN_SQI or no usable data -> recheck /
    too_fatigued=null."""
    legs = int(have_reading) + int(have_voice) + int(have_face)
    low_quality = (sqi is not None and sqi < MIN_SQI)

    if score is None or legs == 0 or low_quality:
        reason = ("Signal insufficient for a confident call"
                  + (f" (SQI {sqi} < {MIN_SQI})" if low_quality else "")
                  + (" — only one modality captured." if legs == 1 else "."))
        return {
            "fatigue_score": score,
            "too_fatigued": None,
            "trial_recommendation": "pause",   # inconclusive -> conservative pause+recheck
            "next_action": "recheck",
            "confidence": 0.2,
            "reasoning": reason + " Re-capture recommended before deciding.",
            "contributions": contribs,
        }

    rec, next_action, too_fatigued = _deterministic_recommendation(score)
    # confidence: distance from the stop threshold, scaled by coverage & quality.
    conf = _clip01(abs(score - THRESHOLD_STOP) / 0.4)
    conf *= 0.6 + 0.13 * legs                       # more modalities -> more confident
    if sqi is not None:
        conf *= _clip01(0.5 + sqi / 2)
    conf = round(_clip01(conf), 2)

    if rec == "stop":
        reasoning = "Fatigue markers exceed the stop threshold — recommend stopping the session."
    elif rec == "pause":
        reasoning = "Fatigue markers are elevated — recommend pausing and re-checking before continuing."
    else:
        reasoning = "Fatigue markers within acceptable range — patient can continue the trial."

    return {
        "fatigue_score": round(score, 3),
        "too_fatigued": bool(too_fatigued),
        "trial_recommendation": rec,
        "next_action": next_action,
        "confidence": conf,
        "reasoning": reasoning,
        "contributions": contribs,
    }


PROMPT = (
    "You are the fatigue-classification node in a CONTACTLESS clinical-trial "
    "vitals-monitoring pipeline. You are given: a deterministic, research-weighted "
    "fatigue_score in [0,1] (>=0.60 = too fatigued / stop, 0.45-0.60 = pause & "
    "recheck, <0.45 = continue), a transparent per-feature contribution breakdown, "
    "and the patient's chart. Decide whether this patient is TOO FATIGUED TO "
    "CONTINUE THE CLINICAL TRIAL. Weigh the deterministic score and its "
    "contributions as your grounding; use the patient chart (age, conditions, "
    "doctor's notes, recent readings/trend) to adjust ONLY when clearly justified "
    "(e.g. a note flagging low baseline energy, or a worsening trend). Do NOT "
    "invent feature values. Be conservative for patient safety: when in doubt "
    "between continue and pause, choose pause. Respond ONLY with compact JSON: "
    '{"too_fatigued": true|false, "trial_recommendation": "continue"|"pause"|"stop", '
    '"next_action": "continue"|"recheck"|"halt", "confidence": 0-1, '
    '"reasoning": "one or two sentences citing the drivers"}'
)


def _pick_provider():
    """Return (name, url, headers, include_model_field) for whichever LLM is
    configured, or None. Preference: Azure OpenAI -> OpenAI -> Anthropic."""
    az_key = os.environ.get("AZURE_OPENAI_API_KEY")
    az_ep = os.environ.get("AZURE_OPENAI_ENDPOINT")
    az_dep = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if az_key and az_ep and az_dep:
        ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
        url = f"{az_ep.rstrip('/')}/openai/deployments/{az_dep}/chat/completions?api-version={ver}"
        return ("azure:" + az_dep, url,
                {"content-type": "application/json", "api-key": az_key}, False)

    oa_key = os.environ.get("OPENAI_API_KEY")
    if oa_key:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        return ("openai:" + model, base + "/chat/completions",
                {"content-type": "application/json",
                 "authorization": f"Bearer {oa_key}"}, True)

    an_key = os.environ.get("ANTHROPIC_API_KEY")
    if an_key:
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        return ("anthropic:" + model, "https://api.anthropic.com/v1/messages",
                {"content-type": "application/json", "x-api-key": an_key,
                 "anthropic-version": "2023-06-01"}, "anthropic")
    return None


def llm_classify(verdict, patient_ctx):
    """Agentic trial-classification: let the LLM classify 'too fatigued to
    continue the trial' from the deterministic score + contributions + patient
    chart. Grounded; deterministic result is the fallback on no-key/error.
    Returns (verdict, engine_name)."""
    prov = _pick_provider()
    if not prov:
        return verdict, ("rule-core (no LLM key: set OPENAI_API_KEY, "
                         "AZURE_OPENAI_* or ANTHROPIC_API_KEY)")
    name, url, headers, model_mode = prov

    payload = {
        "fatigue_score": verdict.get("fatigue_score"),
        "deterministic_recommendation": verdict.get("trial_recommendation"),
        "deterministic_too_fatigued": verdict.get("too_fatigued"),
        "contributions": verdict.get("contributions"),
        "patient_context": patient_ctx,
    }
    user_msg = PROMPT + "\n\n" + json.dumps(payload, default=str)

    if model_mode == "anthropic":
        body = {"model": name.split(":", 1)[1], "max_tokens": 400,
                "messages": [{"role": "user", "content": user_msg}]}
    else:  # OpenAI-style chat completions (OpenAI or Azure)
        body = {"max_tokens": 400, "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": user_msg}]}
        if model_mode is True:  # OpenAI needs the model field; Azure gets it from the URL
            body["model"] = name.split(":", 1)[1]

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        if model_mode == "anthropic":
            text = "".join(b.get("text", "") for b in resp.get("content", []))
        else:
            text = resp["choices"][0]["message"]["content"]
        text = text[text.find("{"): text.rfind("}") + 1]
        llm = json.loads(text)
        verdict = {**verdict, **{k: llm[k] for k in
                   ("too_fatigued", "trial_recommendation", "next_action",
                    "confidence", "reasoning") if k in llm}}
        return verdict, name
    except Exception as e:
        return verdict, f"rule-core ({name} error: {e})"


def assess(reading, voice, patient=None, db_path="vitals.db", use_llm=True):
    """One-call fatigue assessment for the live monitors: fuse -> decide ->
    optional agentic LLM classification -> the full OUTPUT CONTRACT dict.
    `reading` should already carry `face_emotion` (and optionally `voice`) so all
    modalities are scored. Mirrors main()'s wiring; never raises on missing data."""
    if reading is not None and reading.get("voice") and not voice:
        voice = reading.get("voice")
    have_face = bool(isinstance((reading or {}).get("face_emotion"), dict))

    baseline = _load_baseline(patient, db_path) if patient else None
    score, contribs, sqi, n_personal = fuse(reading, voice, baseline)
    verdict = decide(score, contribs, sqi,
                     reading is not None, voice is not None, have_face)
    verdict["baseline"] = _baseline_summary(baseline, n_personal)
    if n_personal and verdict.get("too_fatigued") is not None:
        verdict["confidence"] = round(_clip01(verdict["confidence"] * 1.1), 2)

    engine = "rule-core"
    if use_llm and verdict.get("too_fatigued") is not None:
        patient_ctx = _patient_context(patient, db_path) if patient else None
        verdict, engine = llm_classify(verdict, patient_ctx)

    return {
        "fatigue_score": verdict.get("fatigue_score"),
        "too_fatigued": verdict.get("too_fatigued"),
        "trial_recommendation": verdict.get("trial_recommendation"),
        "next_action": verdict.get("next_action"),
        "confidence": verdict.get("confidence"),
        "reasoning": verdict.get("reasoning"),
        "contributions": verdict.get("contributions", []),
        "weights_rationale": WEIGHTS_RATIONALE,
        "engine": engine,
        "inputs": {
            "heart_leg": reading is not None,
            "voice_leg": voice is not None,
            "face_leg": have_face,
            "sqi": sqi,
            "baseline": verdict.get("baseline"),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reading", default="vitals-dashboard/public/reading.json")
    ap.add_argument("--voice", default="voice_features.json")
    ap.add_argument("--out", default="decision.json")
    ap.add_argument("--patient", help="patient_code -> score against their personal baseline")
    ap.add_argument("--db", default=os.environ.get("VITALS_DB", "vitals.db"),
                    help="SQLite DB for baselines/context (default vitals.db / $VITALS_DB)")
    ap.add_argument("--no-llm", action="store_true", help="skip the agentic LLM layer")
    args = ap.parse_args()

    reading = load(args.reading)
    voice = load(args.voice)
    # Prefer voice embedded in the reading; fall back to the standalone file.
    if reading is not None and reading.get("voice") and not voice:
        voice = reading.get("voice")

    have_face = bool(isinstance((reading or {}).get("face_emotion"), dict))

    baseline = _load_baseline(args.patient, args.db)

    score, contribs, sqi, n_personal = fuse(reading, voice, baseline)
    verdict = decide(score, contribs, sqi,
                     reading is not None, voice is not None, have_face)
    verdict["baseline"] = _baseline_summary(baseline, n_personal)
    # A personal baseline is more trustworthy than absolute cutoffs -> nudge confidence.
    if n_personal and verdict.get("too_fatigued") is not None:
        verdict["confidence"] = round(_clip01(verdict["confidence"] * 1.1), 2)

    engine = "rule-core"
    # Only invoke the LLM when we have a usable deterministic call to ground it.
    if not args.no_llm and verdict.get("too_fatigued") is not None:
        patient_ctx = _patient_context(args.patient, args.db)
        verdict, engine = llm_classify(verdict, patient_ctx)

    # ---- assemble the strict OUTPUT CONTRACT -------------------------------
    decision = {
        "fatigue_score": verdict.get("fatigue_score"),
        "too_fatigued": verdict.get("too_fatigued"),
        "trial_recommendation": verdict.get("trial_recommendation"),
        "next_action": verdict.get("next_action"),
        "confidence": verdict.get("confidence"),
        "reasoning": verdict.get("reasoning"),
        "contributions": verdict.get("contributions", []),
        "weights_rationale": WEIGHTS_RATIONALE,
        "engine": engine,
        "inputs": {
            "heart_leg": reading is not None,
            "voice_leg": voice is not None,
            "face_leg": have_face,
            "sqi": sqi,
            "baseline": verdict.get("baseline"),
        },
    }

    with open(args.out, "w") as f:
        json.dump(decision, f, indent=2)

    # Merge voice + fatigue back into reading.json so the dashboard shows the
    # whole picture from its single "Load reading" fetch.
    if reading is not None and os.path.exists(args.reading):
        reading["voice"] = voice
        reading["fatigue"] = decision
        with open(args.reading, "w") as f:
            json.dump(reading, f)

    _print_decision(decision, engine)
    print(f"Wrote {args.out}")


def _print_decision(d, engine):
    tf = d.get("too_fatigued")
    label = {True: "TOO FATIGUED", False: "OK TO CONTINUE",
             None: "INCONCLUSIVE"}[tf]
    b = (d.get("inputs") or {}).get("baseline") or {}
    print("")
    print("== Fatigue decision " + "=" * 28)
    print(f"| Verdict        : {label}")
    print(f"| Fatigue score  : {d.get('fatigue_score')}  "
          f"(pause>={THRESHOLD_PAUSE}, stop>={THRESHOLD_STOP})")
    print(f"| Trial rec.     : {str(d.get('trial_recommendation')).upper()}")
    print(f"| Next action    : {str(d.get('next_action')).upper()}")
    print(f"| Confidence     : {d.get('confidence')}")
    print(f"| Engine         : {engine}")
    inp = d.get("inputs", {})
    print(f"| Modalities     : heart={inp.get('heart_leg')} "
          f"voice={inp.get('voice_leg')} face={inp.get('face_leg')}")
    if b.get("used"):
        print(f"| Baseline       : personal ({b.get('features_scored')} feats "
              f"from {b.get('from_readings')} {b.get('source')} reading(s))")
    else:
        print("| Baseline       : absolute cutoffs (no personal baseline)")
    print("| Reasoning      :")
    print(f"|   {d.get('reasoning')}")
    print("| Contributions  :")
    for c in sorted(d.get("contributions", []),
                    key=lambda x: x.get("contribution") or 0, reverse=True):
        print(f"|   {c['feature']:<20} val={c['value']!s:<8} "
              f"w={c['weight']:<5} contrib={c['contribution']}  ({c['note']})")
    print("=" * 48)


if __name__ == "__main__":
    main()
