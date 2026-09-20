"""
oversight.py — the automated clinical-oversight engine (replaces the CRA/monitor).

Consumes the mock Vault CDMS (SoA + eCRF forms + deviations via veeva_mock.VaultCDMS)
together with the live contactless-vitals + conversation state:
  - vitals-dashboard/public/reading.json     (vitals + fatigue safety signal)
  - vitals-dashboard/public/agenda_state.json (conversation agenda coverage)
  - vitals-dashboard/public/transcript.json   (running transcript; may be ABSENT)

and produces three live artefacts the dashboard polls:

  A) vitals-dashboard/public/soa_state.json        — per-activity SoA status
  B) vitals-dashboard/public/oversight_state.json  — live checks + deviations
  C) vitals-dashboard/public/monitoring_report.json — CRA-style post-visit report
                                                      (only when --report is passed)

ALCOA-C aware: auto-filled data is always DRAFT with provenance; only the clinician
confirm step marks it confirmed. Oversight NEVER confirms data — it flags what still
needs a clinician e-signature and what the protocol still requires.

Degrades gracefully: missing transcript / agenda / reading / OpenAI key -> sensible
defaults, never crashes. Safe to call repeatedly on a loop (idempotent-ish: it will
not record duplicate deviations — it checks list_deviations first).

CLI:
  conda run -n papagei_env python oversight.py --subject S-001 --visit V2
      -> writes soa_state.json + oversight_state.json
  conda run -n papagei_env python oversight.py --subject S-001 --visit V2 --report
      -> additionally writes monitoring_report.json
"""

import argparse
import json
import os
import time
import urllib.request

from veeva_mock import VaultCDMS

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "vitals-dashboard", "public")
READING = os.path.join(PUBLIC, "reading.json")
AGENDA = os.path.join(PUBLIC, "agenda_state.json")
TRANSCRIPT = os.path.join(PUBLIC, "transcript.json")

SOA_OUT = os.path.join(PUBLIC, "soa_state.json")
OVERSIGHT_OUT = os.path.join(PUBLIC, "oversight_state.json")
REPORT_OUT = os.path.join(PUBLIC, "monitoring_report.json")

OPENAI = "https://api.openai.com/v1"

# Symptom lexicon (mirrors edc_autofill's) — used to detect transcript<->EDC gaps.
_SYMPTOM_LEXICON = {
    "headache": "Headache", "head ache": "Headache", "migraine": "Migraine",
    "nausea": "Nausea", "nauseous": "Nausea", "vomit": "Vomiting",
    "dizzy": "Dizziness", "dizziness": "Dizziness", "lightheaded": "Dizziness",
    "fever": "Pyrexia", "chills": "Chills", "cough": "Cough",
    "rash": "Rash", "itch": "Pruritus", "diarrhea": "Diarrhoea",
    "constipation": "Constipation", "sore throat": "Pharyngitis",
    "shortness of breath": "Dyspnoea", "short of breath": "Dyspnoea",
    "chest pain": "Chest pain", "insomnia": "Insomnia", "can't sleep": "Insomnia",
    "anxiety": "Anxiety", "anxious": "Anxiety", "swelling": "Oedema",
    "palpitation": "Palpitations", "blurred vision": "Vision blurred",
}

_MED_LEXICON = [
    "lisinopril", "metformin", "atorvastatin", "amlodipine", "omeprazole",
    "levothyroxine", "aspirin", "ibuprofen", "acetaminophen", "tylenol",
    "advil", "metoprolol", "losartan", "gabapentin", "sertraline", "albuterol",
    "insulin", "warfarin", "prednisone", "amoxicillin", "hydrochlorothiazide",
    "beta-blocker", "beta blocker",
]


# --------------------------------------------------------------------------- #
# env / io helpers (same dotenv pattern as edc_autofill.py / fatigue_agent.py)
# --------------------------------------------------------------------------- #

def _load_dotenv():
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# form helpers
# --------------------------------------------------------------------------- #

def _non_empty_fields(form):
    return [f for f in form.get("fields", []) if f.get("status") != "empty"
            and f.get("value") is not None]


def _draft_fields(form):
    return [f for f in form.get("fields", []) if f.get("status") == "draft"]


def _confirmed_fields(form):
    return [f for f in form.get("fields", []) if f.get("status") == "confirmed"]


def _field(form, fid):
    for f in form.get("fields", []):
        if f.get("id") == fid:
            return f
    return None


def _short_val(v):
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def _form_evidence(form):
    """A short human summary of what data the form currently holds."""
    parts = []
    for f in _non_empty_fields(form):
        parts.append(f"{f.get('label', f['id'])}={_short_val(f['value'])}")
    return "; ".join(parts[:4])


# --------------------------------------------------------------------------- #
# transcript signals
# --------------------------------------------------------------------------- #

def _transcript_text(transcript, agenda):
    """Combine transcript text + agenda evidence into one lowercase blob for
    keyword gap-detection. Both inputs may be missing."""
    chunks = []
    if isinstance(transcript, dict):
        t = transcript.get("text")
        if isinstance(t, str):
            chunks.append(t)
        for seg in transcript.get("segments", []) or []:
            if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                chunks.append(seg["text"])
    if isinstance(agenda, dict):
        for it in agenda.get("items", []) or []:
            if isinstance(it, dict):
                for k in ("text", "evidence"):
                    if isinstance(it.get(k), str):
                        chunks.append(it[k])
    return " ".join(chunks).lower()


def _mentioned_symptoms(text):
    seen, out = set(), []
    for kw, term in _SYMPTOM_LEXICON.items():
        if kw in text and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def _mentioned_meds(text):
    seen, out = set(), []
    for med in _MED_LEXICON:
        norm = med.replace("-", " ").replace(" ", "")
        if med in text and norm not in seen:
            seen.add(norm)
            out.append(med)
    return out


# --------------------------------------------------------------------------- #
# SoA status (output A)
# --------------------------------------------------------------------------- #

def build_soa_state(cdms, subject, visit, forms_by_id, text_blob):
    """Per required/optional activity: done | suggested | pending.

      done      — the activity's form holds data (draft or confirmed) in the EDC.
      suggested — no EDC data yet, but the transcript/agenda implies it was
                  addressed (e.g. a symptom/med mentioned, or fatigue captured).
      pending   — nothing at all.
    """
    soa = cdms.get_soa(visit)
    symptoms = _mentioned_symptoms(text_blob)
    meds = _mentioned_meds(text_blob)

    activities = []
    pending = []
    required_total = 0
    done_count = 0

    for a in soa:
        fid = a.get("form")
        form = forms_by_id.get(fid, {})
        required = bool(a.get("required"))
        if required:
            required_total += 1

        non_empty = _non_empty_fields(form)
        confirmed = _confirmed_fields(form)

        if non_empty:
            status = "done"
            if confirmed and not _draft_fields(form):
                evidence = f"{_form_evidence(form)} (confirmed)"
            else:
                evidence = f"{_form_evidence(form)} (draft — awaiting confirm)"
            source = "edc"
        else:
            # no EDC data — does the conversation imply it was addressed?
            implied, why = _implied_by_conversation(a["id"], symptoms, meds, text_blob)
            if implied:
                status = "suggested"
                evidence = why
                source = "transcript"
            else:
                status = "pending"
                evidence = "No data captured yet"
                source = None

        if status == "done" and required:
            done_count += 1
        if status != "done":
            pending.append(a["id"])

        activities.append({
            "id": a["id"],
            "name": a.get("name", a["id"]),
            "form": fid,
            "required": required,
            "status": status,
            "evidence": evidence,
            "source": source,
        })

    return {
        "study": cdms.get_study().get("id"),
        "subject": subject,
        "visit": visit,
        "in_window": cdms.visit_in_window(visit),
        "updated_at": time.time(),
        "required_total": required_total,
        "done": done_count,
        "pending": pending,
        "activities": activities,
    }


def _implied_by_conversation(activity_id, symptoms, meds, text_blob):
    """Heuristic: was this activity addressed in conversation even without EDC data?"""
    if activity_id == "ae_review" and symptoms:
        return True, f"Conversation mentioned {', '.join(symptoms[:3])} — AE review implied"
    if activity_id == "conmeds" and meds:
        return True, f"Conversation mentioned medication(s) {', '.join(meds[:3])} — conmed review implied"
    if activity_id == "fatigue_pro":
        # fatigue is discussed / a fatigue reading exists in the conversation blob
        if "fatigue" in text_blob or "tired" in text_blob or "exhausted" in text_blob:
            return True, "Fatigue discussed in conversation — PRO implied"
    return False, ""


# --------------------------------------------------------------------------- #
# Live oversight checks + deviations (output B)
# --------------------------------------------------------------------------- #

def build_oversight_state(cdms, subject, visit, forms_by_id, reading, transcript,
                          agenda, text_blob):
    checks = []
    soa = cdms.get_soa(visit)
    in_window = cdms.visit_in_window(visit)

    # ---- visit window check -------------------------------------------------
    if in_window:
        checks.append({
            "activity": "visit_window",
            "status": "pass",
            "detail": f"Visit {visit} is within its protocol window.",
            "guidance": "No action — proceed with the visit assessments.",
        })
    else:
        checks.append({
            "activity": "visit_window",
            "status": "fail",
            "detail": f"Visit {visit} appears OUTSIDE its protocol window.",
            "guidance": "Confirm the visit date; an out-of-window visit is a protocol "
                        "deviation and must be documented.",
        })

    # ---- per required SoA activity: EDC data present? -----------------------
    missing_required = []
    for a in soa:
        if not a.get("required"):
            continue
        fid = a.get("form")
        form = forms_by_id.get(fid, {})
        non_empty = _non_empty_fields(form)
        drafts = _draft_fields(form)
        confirmed = _confirmed_fields(form)
        name = a.get("name", a["id"])

        if not non_empty:
            checks.append({
                "activity": a["id"],
                "status": "fail",
                "detail": f"Required activity '{name}' has no data in the EDC ({fid} form is empty).",
                "guidance": f"Capture and record the {name} on the {fid} form before sign-off.",
            })
            missing_required.append(a)
        elif drafts:
            checks.append({
                "activity": a["id"],
                "status": "warn",
                "detail": f"'{name}' has {len(drafts)} unconfirmed DRAFT field(s) on {fid} "
                          f"({_form_evidence(form)}).",
                "guidance": f"Review the auto-filled {fid} draft and confirm / e-sign it (ALCOA-C).",
            })
        else:
            checks.append({
                "activity": a["id"],
                "status": "pass",
                "detail": f"'{name}' is captured and confirmed on {fid} "
                          f"({len(confirmed)} field(s) e-signed).",
                "guidance": "No action.",
            })

    # ---- safety: fatigue -----------------------------------------------------
    fatigue = (reading or {}).get("fatigue") or {}
    too_fatigued = bool(fatigue.get("too_fatigued"))
    rec = fatigue.get("trial_recommendation") or fatigue.get("next_action")
    if too_fatigued:
        checks.append({
            "activity": "safety_fatigue",
            "status": "fail",
            "detail": f"Fatigue model flags the subject as TOO FATIGUED to continue "
                      f"(score {fatigue.get('fatigue_score')}, recommendation '{rec}').",
            "guidance": "Pause / stop the session per protocol safety rules; re-assess "
                        "before continuing and document the interruption.",
        })
    elif fatigue:
        checks.append({
            "activity": "safety_fatigue",
            "status": "pass",
            "detail": f"Fatigue within acceptable range (score {fatigue.get('fatigue_score')}, "
                      f"recommendation '{rec}').",
            "guidance": "No action — subject may continue.",
        })

    # ---- transcript <-> EDC gap ---------------------------------------------
    symptoms = _mentioned_symptoms(text_blob)
    meds = _mentioned_meds(text_blob)

    ae_form = forms_by_id.get("AE", {})
    ae_term = _field(ae_form, "ae_term")
    ae_has_term = ae_term is not None and ae_term.get("value") is not None
    ae_confirmed = ae_term is not None and ae_term.get("status") == "confirmed"

    if symptoms:
        if not ae_has_term:
            checks.append({
                "activity": "ae_review",
                "status": "warn",
                "detail": f"Conversation reported symptom(s) {', '.join(symptoms[:3])} "
                          f"but the AE form has no adverse-event term.",
                "guidance": f"Open the Adverse Events form and record the reported "
                            f"{symptoms[0].lower()}.",
            })
        elif not ae_confirmed:
            checks.append({
                "activity": "ae_review",
                "status": "warn",
                "detail": f"AE '{ae_term.get('value')}' is captured as an UNCONFIRMED draft "
                          f"from the transcript ({', '.join(symptoms[:3])} mentioned).",
                "guidance": "Verify the AE against the conversation and confirm / e-sign the "
                            "AE form (grade severity + relatedness).",
            })

    cm_form = forms_by_id.get("CM", {})
    cm_name = _field(cm_form, "cm_name")
    cm_has = cm_name is not None and cm_name.get("value") is not None
    cm_confirmed = cm_name is not None and cm_name.get("status") == "confirmed"

    if meds:
        if not cm_has:
            checks.append({
                "activity": "conmeds",
                "status": "warn",
                "detail": f"Conversation mentioned medication(s) {', '.join(meds[:3])} "
                          f"but the Concomitant Medications form has no entry.",
                "guidance": f"Open the Concomitant Medications form and record {meds[0]}.",
            })
        elif not cm_confirmed:
            checks.append({
                "activity": "conmeds",
                "status": "warn",
                "detail": f"Con-med '{cm_name.get('value')}' captured as an UNCONFIRMED draft "
                          f"from the transcript.",
                "guidance": "Verify the medication (name/dose/indication) and confirm / e-sign "
                            "the CM form.",
            })

    # ---- agenda coverage (informational) ------------------------------------
    if isinstance(agenda, dict) and agenda.get("items"):
        uncovered = [it for it in agenda["items"] if not it.get("covered")]
        if uncovered:
            checks.append({
                "activity": "agenda_coverage",
                "status": "warn",
                "detail": f"{len(uncovered)} planned discussion topic(s) not yet covered "
                          f"in the conversation.",
                "guidance": "Cover remaining agenda topics: "
                            + "; ".join(it.get("text", "?") for it in uncovered[:3]),
            })

    # ---- record genuine deviations (idempotent) -----------------------------
    deviations = _record_deviations(cdms, subject, visit, missing_required,
                                    in_window, too_fatigued)

    # ---- overall roll-up ----------------------------------------------------
    statuses = [c["status"] for c in checks]
    if "fail" in statuses or deviations_open(deviations, "major"):
        overall = "deviation"
    elif "warn" in statuses:
        overall = "attention"
    else:
        overall = "on-track"

    return {
        "study": cdms.get_study().get("id"),
        "subject": subject,
        "visit": visit,
        "updated_at": time.time(),
        "overall": overall,
        "checks": checks,
        "deviations": deviations,
    }


def deviations_open(deviations, severity):
    return any(d.get("severity") == severity for d in deviations)


def _record_deviations(cdms, subject, visit, missing_required, in_window, too_fatigued):
    """Record REAL protocol deviations via veeva_mock — but never duplicate.

    We check list_deviations first and only record a deviation for an activity if
    one is not already on file for this subject/visit. Returns the full list
    (formatted for the oversight output, with guidance)."""
    existing = cdms.list_deviations(subject, visit)
    existing_by_activity = {d["activity"] for d in existing}

    # a required activity still missing data => a real deviation
    for a in missing_required:
        if a["id"] not in existing_by_activity:
            cdms.record_deviation(
                subject, visit, a["id"], "minor",
                f"Required activity '{a.get('name', a['id'])}' has no data captured in the EDC.")
            existing_by_activity.add(a["id"])

    # out-of-window visit => a real deviation
    if not in_window and "visit_window" not in existing_by_activity:
        cdms.record_deviation(
            subject, visit, "visit_window", "major",
            f"Visit {visit} conducted outside its protocol window.")
        existing_by_activity.add("visit_window")

    # too-fatigued but session continued => a real safety deviation
    if too_fatigued and "safety_fatigue" not in existing_by_activity:
        cdms.record_deviation(
            subject, visit, "safety_fatigue", "major",
            "Subject flagged too fatigued to continue by the fatigue model.")
        existing_by_activity.add("safety_fatigue")

    guidance_by_activity = {
        "visit_window": "Document the out-of-window visit and notify the sponsor.",
        "safety_fatigue": "Pause per protocol safety rules and document the interruption.",
    }
    out = []
    for d in cdms.list_deviations(subject, visit):
        g = guidance_by_activity.get(
            d["activity"],
            f"Capture the required '{d['activity']}' data before visit sign-off.")
        out.append({
            "id": d["id"],
            "activity": d["activity"],
            "severity": d["severity"],
            "description": d["description"],
            "guidance": g,
        })
    return out


# --------------------------------------------------------------------------- #
# Post-visit monitoring report (output C)
# --------------------------------------------------------------------------- #

def build_monitoring_report(cdms, subject, visit, forms_by_id, soa_state,
                            oversight_state):
    soa = cdms.get_soa(visit)

    # ---- SDV counts across the visit's forms --------------------------------
    fields_total = auto_sourced = confirmed = pending = 0
    for fid, form in forms_by_id.items():
        for f in form.get("fields", []):
            fields_total += 1
            st = f.get("status")
            if st == "confirmed":
                confirmed += 1
                if f.get("source"):
                    auto_sourced += 1
            elif st == "draft":
                pending += 1
                if f.get("source"):
                    auto_sourced += 1
            # empty fields count toward total only

    # ---- protocol compliance ------------------------------------------------
    required_acts = [a for a in soa if a.get("required")]
    completed, missed = [], []
    for a in required_acts:
        form = forms_by_id.get(a.get("form"), {})
        if _non_empty_fields(form):
            completed.append(a["id"])
        else:
            missed.append(a["id"])

    # ---- queries (open data questions a CRA would raise) --------------------
    queries = _build_queries(forms_by_id, required_acts)

    deviations = oversight_state.get("deviations", [])

    # ---- sign-off readiness -------------------------------------------------
    all_required_confirmed = True
    for a in required_acts:
        form = forms_by_id.get(a.get("form"), {})
        if not _confirmed_fields(form) or _draft_fields(form) or not _non_empty_fields(form):
            all_required_confirmed = False
            break
    signoff_ready = (all_required_confirmed and not missed and not queries
                     and not any(d.get("severity") == "major" for d in deviations))

    compliance = {
        "required": len(required_acts),
        "completed": len(completed),
        "missed": missed,
    }
    sdv = {
        "fields_total": fields_total,
        "auto_sourced": auto_sourced,
        "confirmed": confirmed,
        "pending": pending,
    }

    summary = _narrative(cdms, subject, visit, sdv, compliance, deviations, queries,
                         signoff_ready)
    recommendation = _recommendation(signoff_ready, missed, queries, deviations, pending)

    return {
        "study": cdms.get_study().get("id"),
        "subject": subject,
        "visit": visit,
        "generated_at": time.time(),
        "summary": summary,
        "sdv": sdv,
        "protocol_compliance": compliance,
        "deviations": deviations,
        "queries": queries,
        "signoff_ready": signoff_ready,
        "recommendation": recommendation,
    }


def _build_queries(forms_by_id, required_acts):
    """CRA-style data queries: unresolved fields on captured forms."""
    queries = []
    # AE: draft/present but severity or relatedness not graded
    ae = forms_by_id.get("AE", {})
    ae_term = _field(ae, "ae_term")
    if ae_term is not None and ae_term.get("value") is not None:
        sev = _field(ae, "ae_severity")
        if sev is None or sev.get("value") is None:
            queries.append({"form": "AE", "field": "ae_severity",
                            "query": "Adverse event reported but severity not graded."})
        rel = _field(ae, "ae_related")
        if rel is None or rel.get("value") is None:
            queries.append({"form": "AE", "field": "ae_related",
                            "query": "Adverse event relationship to study drug not assessed."})

    # CM: medication named but dose missing
    cm = forms_by_id.get("CM", {})
    cm_name = _field(cm, "cm_name")
    if cm_name is not None and cm_name.get("value") is not None:
        dose = _field(cm, "cm_dose")
        if dose is None or dose.get("value") is None:
            queries.append({"form": "CM", "field": "cm_dose",
                            "query": "Concomitant medication recorded without a dose."})

    # VS: BP is manual-entry, not auto-filled — flag if missing
    vs = forms_by_id.get("VS", {})
    if _non_empty_fields(vs):
        for bp in ("bp_sys", "bp_dia"):
            f = _field(vs, bp)
            if f is not None and f.get("value") is None:
                queries.append({"form": "VS", "field": bp,
                                "query": "Blood pressure not recorded (manual entry required)."})

    # Any required form still all-draft (unconfirmed) is an open query for sign-off
    for a in required_acts:
        fid = a.get("form")
        form = forms_by_id.get(fid, {})
        if _non_empty_fields(form) and _draft_fields(form) and not _confirmed_fields(form):
            queries.append({"form": fid, "field": "*",
                            "query": f"{form.get('name', fid)} is entirely unconfirmed draft "
                                     f"data — clinician e-signature required."})
    return queries


def _recommendation(signoff_ready, missed, queries, deviations, pending_fields):
    if signoff_ready:
        return "Visit data complete and confirmed — ready for CRA sign-off."
    parts = []
    if missed:
        parts.append(f"capture {len(missed)} missing required activity(ies)")
    if pending_fields:
        parts.append(f"confirm {pending_fields} draft field(s)")
    if queries:
        parts.append(f"resolve {len(queries)} open query(ies)")
    if any(d.get("severity") == "major" for d in deviations):
        parts.append("address the major protocol deviation(s)")
    if not parts:
        parts.append("review outstanding items")
    return "Not ready for sign-off — " + "; ".join(parts) + "."


def _narrative(cdms, subject, visit, sdv, compliance, deviations, queries,
               signoff_ready):
    """LLM narrative (OpenAI) with a deterministic template fallback."""
    facts = {
        "study": cdms.get_study().get("id"),
        "subject": subject,
        "visit": visit,
        "sdv": sdv,
        "protocol_compliance": compliance,
        "n_deviations": len(deviations),
        "deviations": [{"activity": d["activity"], "severity": d["severity"],
                        "description": d["description"]} for d in deviations],
        "n_queries": len(queries),
        "queries": queries,
        "signoff_ready": signoff_ready,
    }
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        text = _openai_narrative(facts, key)
        if text:
            return text
    return _template_narrative(facts)


def _template_narrative(f):
    sdv = f["sdv"]
    comp = f["protocol_compliance"]
    missed = comp["missed"]
    parts = [
        f"Automated monitoring review for subject {f['subject']} at visit {f['visit']} "
        f"({f['study']}).",
        f"Source data verification: {sdv['confirmed']} of {sdv['fields_total']} eCRF "
        f"fields are clinician-confirmed, {sdv['pending']} remain as unconfirmed drafts, "
        f"and {sdv['auto_sourced']} were auto-sourced from contactless capture / transcript "
        f"with full provenance.",
        f"Protocol compliance: {comp['completed']} of {comp['required']} required activities "
        f"have data captured"
        + (f"; missing: {', '.join(missed)}." if missed else "; none missing."),
    ]
    if f["n_deviations"]:
        parts.append(f"{f['n_deviations']} protocol deviation(s) recorded.")
    if f["n_queries"]:
        parts.append(f"{f['n_queries']} data query(ies) raised for the site to resolve.")
    parts.append("Visit is ready for sign-off." if f["signoff_ready"]
                 else "Visit is NOT yet ready for sign-off.")
    return " ".join(parts)


def _openai_narrative(facts, key, timeout=30):
    sys = (
        "You are an experienced clinical research associate (CRA) writing a concise, "
        "factual post-visit monitoring summary for a clinical trial. Use ONLY the JSON "
        "facts provided — do not invent findings. 3-5 sentences, professional tone, "
        "covering SDV status, protocol compliance, deviations, and queries. End by "
        "stating whether the visit is ready for sign-off.")
    body = json.dumps({
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": "FACTS:\n" + json.dumps(facts)},
        ],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        f"{OPENAI}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"   [oversight] OpenAI narrative failed ({e}); using template fallback.")
        return None


# --------------------------------------------------------------------------- #
# main pass
# --------------------------------------------------------------------------- #

def run(subject, visit, report=False, root="veeva",
        reading_path=READING, agenda_path=AGENDA, transcript_path=TRANSCRIPT):
    cdms = VaultCDMS(root=root)

    reading = _read_json(reading_path) or {}
    agenda = _read_json(agenda_path)          # may be None (absent)
    transcript = _read_json(transcript_path)  # may be None (absent)

    text_blob = _transcript_text(transcript, agenda)

    forms = cdms.get_forms(subject, visit)
    forms_by_id = {f["form"]: f for f in forms}

    soa_state = build_soa_state(cdms, subject, visit, forms_by_id, text_blob)
    oversight_state = build_oversight_state(
        cdms, subject, visit, forms_by_id, reading, transcript, agenda, text_blob)

    _atomic_write(SOA_OUT, soa_state)
    _atomic_write(OVERSIGHT_OUT, oversight_state)

    print(f"[oversight] {subject}/{visit}: wrote {SOA_OUT}")
    print(f"[oversight]   SoA: required={soa_state['required_total']} "
          f"done={soa_state['done']} pending={soa_state['pending']}")
    print(f"[oversight] wrote {OVERSIGHT_OUT}")
    print(f"[oversight]   overall={oversight_state['overall']} "
          f"checks={len(oversight_state['checks'])} "
          f"deviations={len(oversight_state['deviations'])}")

    result = {"soa_state": soa_state, "oversight_state": oversight_state}

    if report:
        # re-read forms so the report reflects any deviation-side effects
        forms = cdms.get_forms(subject, visit)
        forms_by_id = {f["form"]: f for f in forms}
        rpt = build_monitoring_report(
            cdms, subject, visit, forms_by_id, soa_state, oversight_state)
        _atomic_write(REPORT_OUT, rpt)
        print(f"[oversight] wrote {REPORT_OUT}")
        print(f"[oversight]   SDV: {rpt['sdv']} | compliance={rpt['protocol_compliance']} "
              f"| queries={len(rpt['queries'])} | signoff_ready={rpt['signoff_ready']}")
        result["monitoring_report"] = rpt

    return result


def main():
    ap = argparse.ArgumentParser(
        description="Automated clinical-oversight engine (SoA status + live oversight "
                    "+ post-visit monitoring report)")
    ap.add_argument("--subject", default="S-001")
    ap.add_argument("--visit", default="V2")
    ap.add_argument("--report", action="store_true",
                    help="also write monitoring_report.json (post-visit CRA report)")
    ap.add_argument("--root", default="veeva")
    ap.add_argument("--reading", default=READING)
    ap.add_argument("--agenda", default=AGENDA)
    ap.add_argument("--transcript", default=TRANSCRIPT)
    args = ap.parse_args()
    run(args.subject, args.visit, report=args.report, root=args.root,
        reading_path=args.reading, agenda_path=args.agenda,
        transcript_path=args.transcript)


if __name__ == "__main__":
    main()
