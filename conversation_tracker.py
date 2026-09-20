"""
Always-listening visit agenda tracker — "context awareness" for the clinician.

Listens to the whole visit (doctor + patient, NOT speaker-isolated), transcribes
it with OpenAI Whisper, and against the patient's must-discuss topics (doctor's
notes tagged `agenda`/`instruction` in db.py) it:
  - checks a topic off once it's actually discussed (with the evidence quote), and
  - keeps prompting for the topics still not covered.

Writes agenda_state.json (dashboard + AR visit panel read it) each round.

Run in 'papagei_env' (needs OPENAI_API_KEY in .env):
    conda activate papagei_env
    python conversation_tracker.py --patient P001
Stop with Ctrl-C.  A whole-room mic is used, so run it INSTEAD of the mic-bound
voice monitor, or point --mic at a second input.
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request

OPENAI = "https://api.openai.com/v1"
CHAT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


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


def _key():
    k = os.environ.get("OPENAI_API_KEY")
    if not k:
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


def coverage_check(transcript, pending, key):
    """Ask the LLM which still-pending topics the transcript now covers.
    Returns {topic_index: evidence_quote} for newly-covered topics."""
    if not pending or not transcript.strip():
        return {}
    listing = "\n".join(f"{i}: {t}" for i, t in pending)
    prompt = (
        "You track whether a clinician covered required visit topics. Below is the "
        "running transcript of a doctor-patient visit, then a numbered list of topics "
        "NOT yet marked covered. Return ONLY JSON: "
        '{"covered": [{"id": <int>, "evidence": "<short quote from transcript>"}]}. '
        "Include a topic id ONLY if the transcript clearly shows it was actually "
        "discussed (not merely related). Evidence must be a real snippet.\n\n"
        f"TRANSCRIPT:\n{transcript[-4000:]}\n\nPENDING TOPICS:\n{listing}"
    )
    body = json.dumps({
        "model": CHAT_MODEL, "temperature": 0, "max_tokens": 500,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        f"{OPENAI}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            txt = json.load(r)["choices"][0]["message"]["content"]
        got = json.loads(txt).get("covered", [])
        return {int(c["id"]): str(c.get("evidence", "")).strip() for c in got}
    except Exception as e:
        print(f"[coverage] LLM error: {e}")
        return {}


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", default="P001")
    ap.add_argument("--mic", type=int, default=1)
    ap.add_argument("--out", default="vitals-dashboard/public/agenda_state.json")
    ap.add_argument("--db", default=None)
    ap.add_argument("--chunk-seconds", type=int, default=12)
    ap.add_argument("--check-every", type=float, default=15.0)
    args = ap.parse_args()

    key = _key()
    topics = agenda_items(args.patient, args.db)
    if not topics:
        raise SystemExit(f"No agenda topics for {args.patient}. Add some:\n"
                         f"  python db.py note --patient {args.patient} "
                         f"--text \"...\" --category agenda")

    items = [{"text": t, "covered": False, "evidence": None, "covered_at": None}
             for t in topics]
    transcript = ""
    t0 = time.time()
    last_check = 0.0
    print(f"[agenda] listening for {args.patient} — {len(items)} topics to cover. "
          f"Ctrl-C to stop.")
    for i, it in enumerate(items):
        print(f"   ☐ {it['text']}")

    tmp = os.path.join(tempfile.gettempdir(), "conv_chunk.wav")
    while True:
        record(args.mic, args.chunk_seconds, tmp)
        try:
            text = transcribe(tmp, key)
        except Exception as e:
            print(f"[whisper] error: {e}")
            continue
        if text:
            transcript += " " + text
            print(f"[heard] {text}")

        now = time.time()
        if now - last_check >= args.check_every:
            last_check = now
            pending = [(i, it["text"]) for i, it in enumerate(items) if not it["covered"]]
            newly = coverage_check(transcript, pending, key)
            for idx, evid in newly.items():
                if 0 <= idx < len(items) and not items[idx]["covered"]:
                    items[idx].update(covered=True, evidence=evid,
                                      covered_at=round(now - t0, 1))
                    print(f"   ✅ covered: {items[idx]['text']}  ← \"{evid}\"")

            covered = sum(1 for it in items if it["covered"])
            pending_txt = [it["text"] for it in items if not it["covered"]]
            _atomic_write(args.out, {
                "patient": args.patient,
                "updated_at": time.time(),
                "elapsed_s": round(now - t0, 1),
                "covered": covered, "total": len(items),
                "items": items,
                "pending": pending_txt,
            })
            if pending_txt:
                print(f"[agenda] {covered}/{len(items)} covered · still to raise: "
                      + "; ".join(pending_txt))
            else:
                print(f"[agenda] ✅ all {len(items)} topics covered.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[agenda] stopped.")
