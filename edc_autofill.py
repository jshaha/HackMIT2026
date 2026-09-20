"""
edc_autofill.py — auto-fill the eCRF as DRAFTS from contactless vitals + conversation.

Reads the live contactless-vitals capture (reading.json / decision.json) and the
running conversation transcript, maps them onto Veeva Vault CDMS eCRF fields, and
writes them via veeva_mock.VaultCDMS.submit_form_data(..., status="draft", ...).

ALCOA-C: everything auto-filled is DRAFT with provenance (source/captured_at/
confidence). NOTHING is ever auto-confirmed — only the clinician confirm step
(dashboard -> confirm_form) marks data confirmed.

Mappings:
  VS  <- reading.json vitals:
          hr          <- reading.hr
          resp_rate   <- reading.br
          vs_method   <- "Contactless rPPG (camera)"
          vs_datetime <- reading.captured_at
        source "contactless-rppg", confidence <- reading.sqi
  PRO <- reading.json fatigue:
          fatigue_score <- fatigue.fatigue_score
          too_fatigued  <- fatigue.too_fatigued
        source "fatigue-model", confidence <- fatigue.confidence
  AE / CM <- transcript.json:
          LLM (OpenAI, JSON mode) EXTRACTS adverse events {term,severity,onset}
          and concomitant meds {name,dose,indication} mentioned in the conversation.
          Written as DRAFT with low confidence + source "transcript-extraction",
          flagged for clinician review. Deterministic keyword fallback if no
          OPENAI_API_KEY (or the call fails).

Then publishes vitals-dashboard/public/edc_forms.json:
  {study, subject, visit, updated_at, forms:[<form_instance>, ...]}
covering ALL forms required at the visit (empty forms included so the dashboard
shows what is still needed).

CLI:
  conda run -n papagei_env python edc_autofill.py --subject S-001 --visit V2 \
      [--transcript PATH]
Designed to be called on a loop by the visit-pipeline orchestrator.
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
DECISION = os.path.join(PUBLIC, "decision.json")
TRANSCRIPT = os.path.join(PUBLIC, "transcript.json")
EDC_OUT = os.path.join(PUBLIC, "edc_forms.json")

OPENAI = "https://api.openai.com/v1"
VS_METHOD = "Contactless rPPG (camera)"


# --------------------------------------------------------------------------- #
# env / io helpers (same dotenv pattern as fatigue_agent.py / conversation_tracker.py)
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


def _reading_captured_iso(reading):
    """Normalize reading.captured_at to an ISO string (it is already ISO here,
    but tolerate an epoch float too)."""
    ca = reading.get("captured_at")
    if isinstance(ca, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ca, timezone.utc).isoformat()
    if isinstance(ca, str) and ca:
        return ca
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# transcript extraction (LLM w/ JSON mode, deterministic fallback)
# --------------------------------------------------------------------------- #

# small symptom lexicon -> canonical AE term (fallback + keyword gate)
_SYMPTOM_LEXICON = {
    "headache": "Headache", "head ache": "Headache", "migraine": "Migraine",
    "nausea": "Nausea", "nauseous": "Nausea", "vomit": "Vomiting",
    "dizzy": "Dizziness", "dizziness": "Dizziness", "lightheaded": "Dizziness",
    "fatigue": "Fatigue", "tired": "Fatigue", "exhausted": "Fatigue",
    "fever": "Pyrexia", "chills": "Chills", "cough": "Cough",
    "rash": "Rash", "itch": "Pruritus", "diarrhea": "Diarrhoea",
    "constipation": "Constipation", "pain": "Pain", "sore throat": "Pharyngitis",
    "shortness of breath": "Dyspnoea", "short of breath": "Dyspnoea",
    "chest pain": "Chest pain", "insomnia": "Insomnia", "can't sleep": "Insomnia",
    "anxiety": "Anxiety", "anxious": "Anxiety", "swelling": "Oedema",
}

_SEVERITY_WORDS = {
    "mild": "mild", "slight": "mild", "a little": "mild", "minor": "mild",
    "moderate": "moderate",
    "severe": "severe", "bad": "severe", "terrible": "severe", "intense": "severe",
    "really bad": "severe", "worst": "severe",
}

# common med lexicon for the deterministic fallback
_MED_LEXICON = [
    "lisinopril", "metformin", "atorvastatin", "amlodipine", "omeprazole",
    "levothyroxine", "aspirin", "ibuprofen", "acetaminophen", "tylenol",
    "advil", "metoprolol", "losartan", "gabapentin", "sertraline", "albuterol",
    "insulin", "warfarin", "prednisone", "amoxicillin", "hydrochlorothiazide",
]

_EXTRACT_SYS = (
    "You are a clinical-trial coordinator's assistant extracting structured data "
    "from a patient-clinician conversation transcript for eCRF DRAFT entry. "
    "Extract ONLY items explicitly mentioned in the transcript. Do not invent. "
    "Return strict JSON with two arrays:\n"
    '{"adverse_events":[{"term":"<MedDRA-style term>","severity":"mild|moderate|severe|null",'
    '"onset":"<free text onset e.g. \'since yesterday\' or null>"}],'
    '"concomitant_meds":[{"name":"<drug name>","dose":"<dose e.g. 10mg or null>",'
    '"indication":"<reason or null>"}]}\n'
    "If nothing is mentioned, return empty arrays. severity must be one of "
    "mild/moderate/severe or null."
)


def _openai_extract(transcript_text, key, timeout=30):
    """Call OpenAI chat completions in JSON mode. Returns dict or None on failure."""
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": _EXTRACT_SYS},
            {"role": "user", "content": "TRANSCRIPT:\n" + transcript_text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{OPENAI}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
        content = resp["choices"][0]["message"]["content"]
        data = json.loads(content)
        aes = data.get("adverse_events", []) or []
        cms = data.get("concomitant_meds", []) or []
        return {"adverse_events": aes, "concomitant_meds": cms, "engine": "openai:gpt-4o-mini"}
    except Exception as e:
        print(f"   [autofill] OpenAI extraction failed ({e}); using keyword fallback.")
        return None


def _keyword_extract(transcript_text):
    """Deterministic lexicon-based extraction — no network, fully reproducible."""
    t = transcript_text.lower()
    aes = []
    seen_terms = set()
    for kw, term in _SYMPTOM_LEXICON.items():
        if kw in t and term not in seen_terms:
            seen_terms.add(term)
            # severity: look at a small window around the keyword
            idx = t.find(kw)
            window = t[max(0, idx - 40): idx + 40]
            severity = None
            for sw, sv in _SEVERITY_WORDS.items():
                if sw in window:
                    severity = sv
                    break
            onset = None
            for cue in ("since yesterday", "since this morning", "last night",
                        "yesterday", "this morning", "a few days ago", "last week"):
                if cue in window:
                    onset = cue
                    break
            aes.append({"term": term, "severity": severity, "onset": onset})

    cms = []
    seen_meds = set()
    import re
    for med in _MED_LEXICON:
        if med in t and med not in seen_meds:
            seen_meds.add(med)
            idx = t.find(med)
            window = t[max(0, idx - 30): idx + 40]
            m = re.search(r"(\d+\s?(?:mg|mcg|ml|units?|g))", window)
            dose = m.group(1).replace(" ", "") if m else None
            cms.append({"name": med.capitalize(), "dose": dose, "indication": None})
    return {"adverse_events": aes, "concomitant_meds": cms, "engine": "keyword-fallback"}


def extract_ae_cm(transcript_text):
    """Extract AEs + conmeds from the transcript. OpenAI JSON mode if a key is
    present, else deterministic keyword fallback. Always returns a dict."""
    if not transcript_text or not transcript_text.strip():
        return {"adverse_events": [], "concomitant_meds": [], "engine": "empty"}
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        res = _openai_extract(transcript_text, key)
        if res is not None:
            return res
    return _keyword_extract(transcript_text)


# --------------------------------------------------------------------------- #
# autofill passes
# --------------------------------------------------------------------------- #

def autofill_vitals(cdms, subject, visit, reading):
    """VS <- reading vitals. source contactless-rppg, confidence <- sqi."""
    if not reading:
        return
    fields = {}
    if reading.get("hr") is not None:
        fields["hr"] = round(float(reading["hr"]), 1)
    if reading.get("br") is not None:
        fields["resp_rate"] = round(float(reading["br"]), 1)
    fields["vs_method"] = VS_METHOD
    fields["vs_datetime"] = _reading_captured_iso(reading)
    conf = reading.get("sqi")
    cdms.submit_form_data(subject, visit, "VS", fields,
                          status="draft", source="contactless-rppg", confidence=conf)


def autofill_pro(cdms, subject, visit, reading):
    """PRO <- reading.fatigue. source fatigue-model."""
    fatigue = (reading or {}).get("fatigue") or {}
    if not fatigue:
        return
    fields = {}
    if fatigue.get("fatigue_score") is not None:
        fields["fatigue_score"] = fatigue["fatigue_score"]
    if fatigue.get("too_fatigued") is not None:
        fields["too_fatigued"] = bool(fatigue["too_fatigued"])
    if not fields:
        return
    conf = fatigue.get("confidence")
    cdms.submit_form_data(subject, visit, "PRO", fields,
                          status="draft", source="fatigue-model", confidence=conf)


def autofill_ae_cm(cdms, subject, visit, transcript_text):
    """AE + CM <- transcript extraction. Always DRAFT, low confidence, flagged."""
    extracted = extract_ae_cm(transcript_text)
    src = "transcript-extraction"
    low_conf = 0.35  # deliberately low: needs clinician review

    aes = extracted.get("adverse_events", [])
    if aes:
        ae = aes[0]  # single-instance mock form: first AE (dashboard flags for review)
        fields = {}
        if ae.get("term"):
            fields["ae_term"] = ae["term"]
        sev = ae.get("severity")
        if sev in ("mild", "moderate", "severe"):
            fields["ae_severity"] = sev
        if ae.get("onset"):
            fields["ae_onset"] = ae["onset"]
        if fields:
            cdms.submit_form_data(subject, visit, "AE", fields,
                                  status="draft", source=src, confidence=low_conf)

    cms = extracted.get("concomitant_meds", [])
    if cms:
        cm = cms[0]
        fields = {}
        if cm.get("name"):
            fields["cm_name"] = cm["name"]
        if cm.get("dose"):
            fields["cm_dose"] = cm["dose"]
        if cm.get("indication"):
            fields["cm_indication"] = cm["indication"]
        if fields:
            cdms.submit_form_data(subject, visit, "CM", fields,
                                  status="draft", source=src, confidence=low_conf)

    return extracted


# --------------------------------------------------------------------------- #
# main pass
# --------------------------------------------------------------------------- #

def run(subject, visit, transcript_path=None, root="veeva"):
    cdms = VaultCDMS(root=root)

    reading = _read_json(READING) or {}
    # decision.json is optional context (kept for parity with the contract inputs)
    _ = _read_json(DECISION) or {}

    tpath = transcript_path or TRANSCRIPT
    transcript = _read_json(tpath) or {}
    transcript_text = transcript.get("text", "") if isinstance(transcript, dict) else ""

    autofill_vitals(cdms, subject, visit, reading)
    autofill_pro(cdms, subject, visit, reading)
    extracted = autofill_ae_cm(cdms, subject, visit, transcript_text)

    # publish the whole EDC picture: ALL forms required at the visit, empties too
    forms = cdms.get_forms(subject, visit)
    out = {
        "study": cdms.get_study().get("id"),
        "subject": subject,
        "visit": visit,
        "updated_at": time.time(),
        "forms": forms,
    }
    _atomic_write(EDC_OUT, out)

    n_draft = sum(1 for f in forms for fld in f["fields"] if fld["status"] == "draft")
    print(f"[autofill] {subject}/{visit}: wrote {EDC_OUT}")
    print(f"[autofill] forms={[f['form'] for f in forms]} draft_fields={n_draft} "
          f"extract_engine={extracted.get('engine')} "
          f"AE={len(extracted.get('adverse_events', []))} "
          f"CM={len(extracted.get('concomitant_meds', []))}")
    return out


def main():
    ap = argparse.ArgumentParser(description="EDC auto-fill (DRAFT) from vitals + transcript")
    ap.add_argument("--subject", default="S-001")
    ap.add_argument("--visit", default="V2")
    ap.add_argument("--transcript", default=None,
                    help=f"transcript json path (default {TRANSCRIPT})")
    ap.add_argument("--root", default="veeva")
    args = ap.parse_args()
    run(args.subject, args.visit, transcript_path=args.transcript, root=args.root)


if __name__ == "__main__":
    main()
