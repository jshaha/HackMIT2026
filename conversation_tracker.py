"""
Always-listening visit agenda tracker — "context awareness" for the clinician.

Listens to the whole visit (doctor + patient, NOT speaker-isolated), transcribes
it with OpenAI Whisper, and against the patient's must-discuss topics (doctor's
notes tagged `agenda`/`instruction` in db.py) it:
  - SUGGESTS a topic (status 'suggested') once the rolling transcript looks like
    it discussed it — detected with fast local VECTOR SEARCH (cosine similarity,
    no per-check LLM latency) — carrying the best-matching snippet as evidence
    and the similarity score, and
  - keeps prompting for the topics still 'pending'.

It NEVER auto-marks a topic 'covered'. The dashboard confirms 'suggested' ->
'covered'; this process only ever emits 'pending' or 'suggested'.

Matching backend (see agenda_match.py): sentence-transformers "all-MiniLM-L6-v2"
cosine (method "minilm-cosine", threshold 0.45) with a pure-numpy TF-IDF cosine
fallback (method "tfidf-cosine", threshold 0.22) if the model can't load.

Writes agenda_state.json each round (dashboard + AR visit panel read it).
On stop (Ctrl-C) — or with `--summarize` — it also writes an after-visit
summary to vitals-dashboard/public/visit_summary.json.

Run in 'papagei_env' (needs OPENAI_API_KEY in .env for Whisper + LLM summary):
    conda activate papagei_env
    python conversation_tracker.py --patient P001
    python conversation_tracker.py --patient P001 --summarize   # summarize only
Stop the live loop with Ctrl-C.  A whole-room mic is used, so run it INSTEAD of
the mic-bound voice monitor, or point --mic at a second input.
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request

import agenda_match

OPENAI = "https://api.openai.com/v1"
CHAT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "vitals-dashboard", "public")
SUMMARY_PATH = os.path.join(PUBLIC_DIR, "visit_summary.json")
TRANSCRIPT_PATH = os.path.join(PUBLIC_DIR, "transcript.json")
# Demo subject the Veeva/EDC layer keys off (see VEEVA_CONTRACT.md).
DEFAULT_SUBJECT = os.environ.get("VEEVA_SUBJECT", "S-001")


def _load_dotenv():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _key(required=True):
    k = os.environ.get("OPENAI_API_KEY")
    if not k and required:
        raise SystemExit("OPENAI_API_KEY not set (put it in .env). Whisper needs it.")
    return k


def agenda_items(patient_code, db_path):
    """Must-discuss topics = the patient's agenda/instruction doctor's notes."""
    try:
        import db
        kw = {} if db_path is None else {"db_path": db_path}
        p = db.get_patient(patient_code, **kw)
        if not p:
            return []
        items, seen = [], set()
        for n in db.get_doctor_notes(p["id"], **kw):
            if n.get("category") in ("agenda", "instruction") and n["note"] not in seen:
                seen.add(n["note"])
                items.append(n["note"])
        return items
    except Exception as e:
        print(f"[agenda] db unavailable ({e}); no topics loaded.")
        return []


def soa_agenda_items(visit_id):
    """SoA-driven agenda topics: required activities at `visit_id` mapped to
    must-discuss topic strings, pulled from veeva_mock.get_soa(visit_id).

    Import is guarded — if veeva_mock (or its data) is unavailable, returns []
    so the default agenda behavior is never broken.
    """
    if not visit_id:
        return []
    try:
        import veeva_mock
    except Exception as e:
        print(f"[agenda] veeva_mock unavailable ({e}); no SoA topics loaded.")
        return []
    try:
        cdms = veeva_mock.VaultCDMS(
            root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "veeva"))
        acts = cdms.get_soa(visit_id)
    except Exception as e:
        print(f"[agenda] SoA load failed for {visit_id} ({e}); no SoA topics.")
        return []
    items, seen = [], set()
    for a in acts:
        if not a.get("required", False):
            continue
        name = a.get("name") or a.get("id")
        if name and name not in seen:
            seen.add(name)
            items.append(name)
    print(f"[agenda] SoA @ {visit_id}: {len(items)} required activities as topics.")
    return items


def merge_topics(*topic_lists):
    """De-dup a set of topic lists, preserving first-seen order."""
    seen, out = set(), []
    for lst in topic_lists:
        for t in lst or []:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def export_transcript(patient, transcript, segments, elapsed_s,
                      subject=DEFAULT_SUBJECT, out_path=TRANSCRIPT_PATH):
    """Publish the running transcript for the EDC/oversight layer.

    Writes vitals-dashboard/public/transcript.json:
      {patient, subject, updated_at, elapsed_s, text, segments:[{t,text},...]}
    Called each round and on summary. Additive — does not touch agenda_state.
    """
    _atomic_write(out_path, {
        "patient": patient,
        "subject": subject,
        "updated_at": time.time(),
        "elapsed_s": round(float(elapsed_s), 1),
        "text": transcript.strip(),
        "segments": [{"t": round(float(s["t"]), 1), "text": s["text"]}
                     for s in segments],
    })
    return out_path


def record(mic, seconds, path):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "avfoundation", "-i", f":{mic}",
         "-t", str(seconds), "-ac", "1", "-ar", "16000", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def transcribe(wav_path, key):
    """OpenAI Whisper transcription via a hand-rolled multipart POST (no SDK)."""
    with open(wav_path, "rb") as f:
        audio = f.read()
    b = "----vitalsagenda7f3a2b"
    pre = (f"--{b}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
           f"--{b}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\ntext\r\n"
           f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
           f"Content-Type: audio/wav\r\n\r\n").encode()
    body = pre + audio + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(
        f"{OPENAI}/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode().strip()


def suggest_topics(transcript, items, elapsed_s):
    """Update `items` in place from a local cosine-similarity match of the
    rolling transcript against each topic. Sets status 'suggested' (never
    'covered') when a topic's best similarity clears the threshold; otherwise
    leaves it 'pending'. Returns the matcher method string.

    Topics the clinician has already confirmed 'covered' (via the dashboard,
    reflected back into agenda_state.json / kept in memory) are left untouched.
    """
    topics = [it["text"] for it in items]
    results, method = agenda_match.match_topics(topics, transcript)
    for it, r in zip(items, results):
        if it["status"] == "covered":
            continue  # clinician already confirmed; don't downgrade
        it["similarity"] = r["similarity"]
        if r["suggested"]:
            if it["status"] != "suggested":
                print(f"   ❓ suggested: {it['text']}  ← \"{r['evidence']}\" "
                      f"(sim {r['similarity']:.2f})")
            it["status"] = "suggested"
            it["evidence"] = r["evidence"]
        else:
            it["status"] = "pending"
            it["evidence"] = None
    return method


def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_agenda_state(path, patient, items, elapsed_s, method):
    covered = sum(1 for it in items if it["status"] == "covered")
    pending_txt = [it["text"] for it in items if it["status"] == "pending"]
    _atomic_write(path, {
        "patient": patient,
        "updated_at": time.time(),
        "elapsed_s": round(elapsed_s, 1),
        "covered": covered,
        "total": len(items),
        "items": [
            {
                "id": it["id"],
                "text": it["text"],
                "status": it["status"],
                "similarity": it["similarity"],
                "evidence": it["evidence"],
                "covered_at": it["covered_at"],
            }
            for it in items
        ],
        "pending": pending_txt,
        "method": method,
    })
    return covered, pending_txt


# --------------------------------------------------------------------------- #
# after-visit summary
# --------------------------------------------------------------------------- #
def _collect_vitals():
    """Pull hr / fatigue / recommendation from the latest reading + decision."""
    hr_avg = fatigue_peak = final_rec = None
    reading = _read_json(os.path.join(PUBLIC_DIR, "reading.json"))
    if reading:
        hr = reading.get("hr")
        if isinstance(hr, (int, float)):
            hr_avg = round(float(hr), 1)
        fat = (reading.get("fatigue") or {}).get("fatigue_score")
        vfat = (reading.get("voice") or {}).get("fatigue_index")
        cands = [x for x in (fat, vfat) if isinstance(x, (int, float))]
        if cands:
            fatigue_peak = round(max(float(c) for c in cands), 3)
    decision = (_read_json(os.path.join(PUBLIC_DIR, "decision.json"))
                or (reading or {}).get("fatigue"))
    if decision:
        na = decision.get("next_action")
        rsn = decision.get("reasoning")
        final_rec = " — ".join(str(x) for x in (na, rsn) if x) or None
    return {"hr_avg": hr_avg, "fatigue_peak": fatigue_peak,
            "final_recommendation": final_rec}


def _emotional_arc(reading):
    """One-line qualitative arc from the voice emotion snapshot (best effort)."""
    voice = (reading or {}).get("voice") or {}
    state = voice.get("emotional_state")
    arousal = voice.get("arousal_index")
    valence = voice.get("valence")
    if state:
        extra = []
        if isinstance(arousal, (int, float)):
            extra.append(f"arousal {arousal:.2f}")
        if isinstance(valence, (int, float)):
            extra.append(f"valence {valence:.2f}")
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"Patient presented as {state}{tail} during the visit."
    return "No voice-emotion signal was captured for this visit."


def _llm_summary(patient, transcript, covered, missed, vitals, key):
    prompt = (
        "You are a clinical scribe. Write a concise (3-5 sentence) after-visit "
        "summary for the clinician from the transcript below. Be factual, do not "
        "invent findings. Then note which agenda topics were covered and which "
        "were missed.\n\n"
        f"PATIENT: {patient}\n"
        f"AGENDA TOPICS COVERED/DISCUSSED: {covered or 'none'}\n"
        f"AGENDA TOPICS MISSED: {missed or 'none'}\n"
        f"VITALS: hr_avg={vitals['hr_avg']}, fatigue_peak={vitals['fatigue_peak']}, "
        f"recommendation={vitals['final_recommendation']}\n\n"
        f"TRANSCRIPT:\n{transcript[-6000:]}"
    )
    body = json.dumps({
        "model": CHAT_MODEL, "temperature": 0.2, "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        f"{OPENAI}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def _template_summary(patient, transcript, covered, missed, vitals):
    words = len(transcript.split())
    bits = [f"Visit with {patient}: ~{words} words of conversation transcribed."]
    if covered:
        bits.append("Topics discussed: " + "; ".join(covered) + ".")
    if missed:
        bits.append("Not yet raised: " + "; ".join(missed) + ".")
    if vitals["hr_avg"] is not None:
        bits.append(f"Heart rate ~{vitals['hr_avg']} bpm.")
    if vitals["final_recommendation"]:
        bits.append(f"Recommendation: {vitals['final_recommendation']}.")
    return " ".join(bits)


def summarize_visit(patient, transcript, items, duration_s, out_path=SUMMARY_PATH):
    """Write vitals-dashboard/public/visit_summary.json.

    Uses the OpenAI LLM if a key is present (not latency-critical here), with a
    deterministic template fallback otherwise. `items` may be the live in-memory
    items or None (then covered/missed are derived from the transcript match).
    """
    if items is None:
        # standalone --summarize with no live state: derive from a fresh match
        topics = summarize_visit._topics or []
        results, _ = agenda_match.match_topics(topics, transcript)
        items = [{"text": t, "status": ("suggested" if r["suggested"] else "pending")}
                 for t, r in zip(topics, results)]

    covered = [it["text"] for it in items
               if it.get("status") in ("covered", "suggested")]
    missed = [it["text"] for it in items if it.get("status") == "pending"]
    vitals = _collect_vitals()
    reading = _read_json(os.path.join(PUBLIC_DIR, "reading.json"))
    arc = _emotional_arc(reading)

    key = _key(required=False)
    summary = None
    if key and transcript.strip():
        try:
            summary = _llm_summary(patient, transcript, covered, missed, vitals, key)
        except Exception as e:
            print(f"[summary] LLM error ({e}); using template.")
    if not summary:
        summary = _template_summary(patient, transcript, covered, missed, vitals)

    out = {
        "patient": patient,
        "generated_at": time.time(),
        "duration_s": round(float(duration_s), 1),
        "summary": summary,
        "topics_covered": covered,
        "topics_missed": missed,
        "vitals": vitals,
        "emotional_arc": arc,
    }
    _atomic_write(out_path, out)
    print(f"[summary] wrote {out_path}")
    return out


summarize_visit._topics = []  # optional topic hint for standalone --summarize


def _has_speech(path, min_rms=0.009):
    """True only if the chunk has real speech energy. Gates out silence so
    Whisper doesn't hallucinate (on quiet audio it invents YouTube-caption
    phrases like 'thanks for watching, subscribe', emojis and all)."""
    import numpy as np
    import soundfile as sf
    try:
        x, _ = sf.read(path)
        if getattr(x, "ndim", 1) > 1:
            x = x.mean(axis=1)
        return float(np.sqrt(np.mean(x ** 2) + 1e-12)) >= min_rms
    except Exception:
        return True   # if unsure, let it through


import re as _re
_EMOJI = _re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "←-⇿⌀-⏿⬀-⯿︀-️]")
_HALLUCINATION_MARKERS = (
    "thanks for watching", "thank you for watching", "subscribe",
    "like and subscribe", "see you next time", "see you in the next",
    "don't forget to", "my channel", "for watching", "see you guys",
)


def _clean_transcription(text):
    """Strip emojis and drop chunks that are Whisper silence-hallucinations.
    Returns '' if the text should be discarded."""
    t = _EMOJI.sub("", text or "").strip()
    low = t.lower()
    if not low:
        return ""
    if any(m in low for m in _HALLUCINATION_MARKERS):
        return ""
    if low in ("thank you.", "thank you", "you", "bye.", "bye", "."):
        return ""
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", default="P001")
    ap.add_argument("--mic", type=int, default=1)
    ap.add_argument("--out", default="vitals-dashboard/public/agenda_state.json")
    ap.add_argument("--db", default=None)
    ap.add_argument("--chunk-seconds", type=int, default=12)
    ap.add_argument("--check-every", type=float, default=15.0)
    ap.add_argument("--threshold", type=float, default=None,
                    help="cosine threshold override (default: matcher default)")
    ap.add_argument("--summarize", action="store_true",
                    help="write visit_summary.json from an existing transcript "
                         "(--transcript / --transcript-file) and exit; no mic.")
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--transcript-file", default=None)
    ap.add_argument("--soa-visit", default=None,
                    help="ALSO source agenda topics from the SoA required "
                         "activities for this visit id (e.g. V2), via "
                         "veeva_mock.get_soa; merged with db doctor-notes topics.")
    ap.add_argument("--subject", default=DEFAULT_SUBJECT,
                    help="clinical-trial subject id stamped into transcript.json")
    args = ap.parse_args()

    topics = merge_topics(agenda_items(args.patient, args.db),
                          soa_agenda_items(args.soa_visit))

    # ---- standalone summary mode (no mic, no loop) -----------------------
    if args.summarize:
        transcript = args.transcript or ""
        if args.transcript_file:
            with open(args.transcript_file) as f:
                transcript += (" " + f.read())
        summarize_visit._topics = topics
        transcript = transcript.strip()
        # keep the transcript export in sync even in standalone summary mode
        segs = [{"t": 0.0, "text": transcript}] if transcript else []
        export_transcript(args.patient, transcript, segs, 0.0,
                          subject=args.subject)
        summarize_visit(args.patient, transcript, None, 0.0)
        return

    if not topics:
        raise SystemExit(f"No agenda topics for {args.patient}. Add some:\n"
                         f"  python db.py note --patient {args.patient} "
                         f"--text \"...\" --category agenda")

    key = _key()
    items = [{"id": i, "text": t, "status": "pending", "similarity": None,
              "evidence": None, "covered_at": None}
             for i, t in enumerate(topics)]
    transcript = ""
    segments = []
    method = "minilm-cosine"
    t0 = time.time()
    last_check = 0.0
    print(f"[agenda] listening for {args.patient} — {len(items)} topics to cover. "
          f"Ctrl-C to stop.")
    for it in items:
        print(f"   ☐ {it['text']}")

    tmp = os.path.join(tempfile.gettempdir(), "conv_chunk.wav")
    try:
        while True:
            record(args.mic, args.chunk_seconds, tmp)
            if not _has_speech(tmp):
                print("[voice] (silence — skipped)")
                continue
            try:
                text = _clean_transcription(transcribe(tmp, key))
            except Exception as e:
                print(f"[whisper] error: {e}")
                continue
            if text:
                transcript += " " + text
                segments.append({"t": time.time() - t0, "text": text})
                print(f"[heard] {text}")

            # publish the running transcript every round for the EDC/oversight layer
            try:
                export_transcript(args.patient, transcript, segments,
                                  time.time() - t0, subject=args.subject)
            except Exception as e:
                print(f"[transcript] export failed: {e}")

            now = time.time()
            if now - last_check >= args.check_every:
                last_check = now
                if args.threshold is not None:
                    # apply custom threshold via a direct match, then map
                    results, method = agenda_match.match_topics(
                        [it["text"] for it in items], transcript,
                        threshold=args.threshold)
                    for it, r in zip(items, results):
                        if it["status"] == "covered":
                            continue
                        it["similarity"] = r["similarity"]
                        it["status"] = "suggested" if r["suggested"] else "pending"
                        it["evidence"] = r["evidence"]
                else:
                    method = suggest_topics(transcript, items, now - t0)

                covered, pending_txt = _write_agenda_state(
                    args.out, args.patient, items, now - t0, method)
                if pending_txt:
                    print(f"[agenda] {covered}/{len(items)} confirmed · still to raise: "
                          + "; ".join(pending_txt))
                else:
                    print(f"[agenda] all topics have evidence (awaiting confirmation).")
    except KeyboardInterrupt:
        print("\n[agenda] stopping — writing after-visit summary…")
        try:
            _write_agenda_state(args.out, args.patient, items,
                                time.time() - t0, method)
            export_transcript(args.patient, transcript, segments,
                              time.time() - t0, subject=args.subject)
            summarize_visit(args.patient, transcript.strip(), items,
                            time.time() - t0)
        except Exception as e:
            print(f"[summary] failed: {e}")
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[agenda] stopped.")
