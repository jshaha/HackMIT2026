"""
Thin SQLite persistence layer for the vitals pipeline.

Ingests the exact dicts the capture scripts already emit:
  - reading.json       (export_reading.py) -> heart leg  (HR/HRV/BVP/embedding)
  - voice_features.json(voice_decoder.py)  -> voice leg  (acoustic markers)
  - a FatigueDecision  (agentic loop, not built yet) -> one per reading

Model: one patient -> many readings (capture events). A reading may carry a
heart leg, a voice leg, or both — each modelled as its own row so either can be
null. Waveforms/embeddings are stored as JSON (fine at this scale). This is
patient health data: identifiers are kept minimal (opaque patient_code), and
the DB file (*.db) is git-ignored.

No third-party deps — just the stdlib.

Doctor's notes give the agentic loop *context awareness* about the patient
(history, meds, instructions) so it doesn't reason from vitals alone.

Library use (from the capture scripts / agentic loop):
    import db
    pid = db.insert_patient("P001")                 # get-or-create by code
    rid = db.insert_reading(pid, reading=reading_d)  # heart leg
    db.add_voice_features(rid, voice_d)              # attach voice leg
    db.insert_fatigue_decision(rid, decision_d)
    rows = db.get_readings(pid)                       # longitudinal, oldest->newest
    db.insert_doctor_note(pid, "Post-op week 2; on beta-blockers.", author="Dr.Lee")
    ctx = db.format_patient_context("P001")          # text to drop into the LLM prompt
    base = db.get_baseline(pid)                       # per-feature mean/std for z-scoring

Per-patient baselines: fatigue is within-subject, so the agent scores personal
deviations. Tag rested captures with is_baseline=True (or `--baseline`); with none
tagged, get_baseline() falls back to the patient's prior non-fatigued readings.

One-line ingest of existing JSON files:
    python db.py ingest   --patient P001 --reading reading.json --voice voice_features.json
    python db.py ingest   --patient P001 --voice rested.json --baseline   # reference capture
    python db.py baseline --patient P001              # show computed per-feature baseline
    python db.py note     --patient P001 --text "Chronic fatigue Hx; sleeps poorly." --author Dr.Lee
    python db.py context  --patient P001              # demographics + notes + recent readings
    python db.py list     --patient P001
    python db.py init
"""
import argparse
import json
import os
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("VITALS_DB", "vitals.db")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS patients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code  TEXT NOT NULL UNIQUE,   -- opaque id; avoid real names (PII)
    alias         TEXT,
    dob           TEXT,                    -- ISO date, optional
    age           INTEGER,                 -- optional (use instead of dob)
    sex           TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL
);

-- One capture event. Legs live in the two feature tables below.
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    captured_at   TEXT NOT NULL,           -- ISO8601 UTC
    source        TEXT,                    -- e.g. "iPhone (Continuity)"
    is_baseline   INTEGER NOT NULL DEFAULT 0,  -- 1 = a rested/normal reference capture
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_patient_time
    ON readings(patient_id, captured_at);

-- Heart leg (from reading.json). hrv.* flattened for trending; waveforms as JSON.
CREATE TABLE IF NOT EXISTS heart_features (
    reading_id       INTEGER PRIMARY KEY REFERENCES readings(id) ON DELETE CASCADE,
    duration_s       REAL,
    fs               REAL,
    hr               REAL,
    sqi              REAL,
    hrv_sdnn         REAL,
    hrv_rmssd        REAL,
    hrv_pnn50        REAL,
    hrv_lf_hf        REAL,
    hrv_breathingrate REAL,
    bvp_json         TEXT,                 -- JSON array of floats (~1800)
    embedding_json   TEXT                  -- JSON {n_segments, dim, mean:[...]}
);

-- Voice leg (from voice_features.json). One row, columns mirror the JSON keys.
CREATE TABLE IF NOT EXISTS voice_features (
    reading_id           INTEGER PRIMARY KEY REFERENCES readings(id) ON DELETE CASCADE,
    duration_s           REAL,
    sr                   INTEGER,
    f0_mean_hz           REAL,
    f0_std_hz            REAL,
    jitter_pct           REAL,
    shimmer_pct          REAL,
    voiced_ratio         REAL,
    pause_ratio          REAL,
    speech_rate_hz       REAL,
    rms_mean             REAL,
    rms_std              REAL,
    spectral_centroid_hz REAL,
    arousal_index        REAL,
    fatigue_index        REAL
);

-- Output of the agentic loop. One per reading.
CREATE TABLE IF NOT EXISTS fatigue_decisions (
    reading_id     INTEGER PRIMARY KEY REFERENCES readings(id) ON DELETE CASCADE,
    too_fatigued   INTEGER,               -- bool (0/1)
    confidence     REAL,                  -- 0-1
    reasoning      TEXT,
    model          TEXT,
    features_json  TEXT,                  -- snapshot of features the model saw
    created_at     TEXT NOT NULL
);

-- Free-text clinician context about a patient (longitudinal, many per patient).
-- Fed to the agentic loop so it reasons with knowledge of the patient's history.
CREATE TABLE IF NOT EXISTS doctor_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id   INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    note         TEXT NOT NULL,           -- the note body
    author       TEXT,                    -- clinician name/id, optional
    category     TEXT,                    -- optional tag e.g. history/medication/instruction
    note_date    TEXT,                    -- clinical date (ISO), defaults to created_at
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_patient_time
    ON doctor_notes(patient_id, note_date);
"""


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path=DEFAULT_DB):
    """Connection with foreign keys on and dict-like rows. Commits on success."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path=DEFAULT_DB):
    """Create tables/indexes if absent, then run lightweight migrations. Idempotent."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)
    return db_path


def _migrate(conn):
    """Additive column migrations for DBs created before a column existed."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(readings)").fetchall()]
    if "is_baseline" not in cols:
        conn.execute("ALTER TABLE readings ADD COLUMN is_baseline INTEGER NOT NULL DEFAULT 0")


# ---------------------------------------------------------------- patients ---
def insert_patient(patient_code, alias=None, dob=None, age=None, sex=None,
                   notes=None, db_path=DEFAULT_DB):
    """Get-or-create a patient by opaque code. Returns patient id."""
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM patients WHERE patient_code = ?", (patient_code,)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO patients (patient_code, alias, dob, age, sex, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (patient_code, alias, dob, age, sex, notes, _utcnow()),
        )
        return cur.lastrowid


# ---------------------------------------------------------------- readings ---
def insert_reading(patient_id, reading=None, voice=None, captured_at=None,
                   source=None, is_baseline=False, db_path=DEFAULT_DB):
    """Create a capture event and attach whichever legs are provided.

    `reading` / `voice` are the dicts from reading.json / voice_features.json.
    captured_at defaults to reading['captured_at'] (heart leg) or now() — the
    voice JSON carries no timestamp of its own. Set is_baseline=True for a
    rested/normal reference capture (feeds get_baseline). Returns the reading id.
    """
    init_db(db_path)
    when = captured_at or (reading or {}).get("captured_at") or _utcnow()
    src = source or (reading or {}).get("source")
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO readings (patient_id, captured_at, source, is_baseline, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (patient_id, when, src, int(bool(is_baseline)), _utcnow()),
        )
        rid = cur.lastrowid
        if reading is not None:
            _write_heart(conn, rid, reading)
        if voice is not None:
            _write_voice(conn, rid, voice)
        return rid


def add_heart_features(reading_id, reading, db_path=DEFAULT_DB):
    """Attach/replace the heart leg on an existing reading (dict from reading.json)."""
    with connect(db_path) as conn:
        _write_heart(conn, reading_id, reading)
    return reading_id


def add_voice_features(reading_id, voice, db_path=DEFAULT_DB):
    """Attach/replace the voice leg on an existing reading (dict from voice_features.json)."""
    with connect(db_path) as conn:
        _write_voice(conn, reading_id, voice)
    return reading_id


def insert_fatigue_decision(reading_id, decision, db_path=DEFAULT_DB):
    """Store the agentic loop's FatigueDecision for a reading (one per reading).

    `decision` keys: too_fatigued(bool), confidence(float), reasoning(str),
    model(str), features(dict snapshot). Missing keys are stored as null.
    """
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fatigue_decisions "
            "(reading_id, too_fatigued, confidence, reasoning, model, features_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                reading_id,
                _asbool(decision.get("too_fatigued")),
                decision.get("confidence"),
                decision.get("reasoning"),
                decision.get("model"),
                _dumps(decision.get("features") or decision.get("feature_snapshot")),
                decision.get("created_at") or _utcnow(),
            ),
        )
    return reading_id


# -------------------------------------------------------------- doctor notes ---
def insert_doctor_note(patient_id, note, author=None, category=None,
                       note_date=None, db_path=DEFAULT_DB):
    """Add a doctor's note for a patient. Returns the note id.

    note_date is the clinical date (ISO); defaults to now. Many notes per patient.
    """
    init_db(db_path)
    now = _utcnow()
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO doctor_notes (patient_id, note, author, category, note_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, note, author, category, note_date or now, now),
        )
        return cur.lastrowid


def get_doctor_notes(patient_id, db_path=DEFAULT_DB):
    """All notes for a patient, oldest->newest."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM doctor_notes WHERE patient_id = ? ORDER BY note_date ASC, id ASC",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_patient_context(patient, recent=3, db_path=DEFAULT_DB):
    """Everything the agentic loop needs to reason *about this patient*:
    demographics + doctor's notes + a compact summary of recent readings.

    `patient` is a patient_code (str) or patient id (int). Returns None if unknown.
    """
    with connect(db_path) as conn:
        p = _resolve_patient(conn, patient)
        if not p:
            return None
        notes = [dict(r) for r in conn.execute(
            "SELECT * FROM doctor_notes WHERE patient_id = ? ORDER BY note_date ASC, id ASC",
            (p["id"],)).fetchall()]
        rrows = conn.execute(
            "SELECT id FROM readings WHERE patient_id = ? ORDER BY captured_at DESC LIMIT ?",
            (p["id"], recent)).fetchall()
        recent_readings = []
        for rr in rrows:
            full = _load_reading(conn, rr["id"])
            recent_readings.append({
                "captured_at": full["captured_at"],
                "hr": full["reading"]["hr"] if full["reading"] else None,
                "fatigue_index": full["voice"]["fatigue_index"] if full["voice"] else None,
                "too_fatigued": full["fatigue"]["too_fatigued"] if full["fatigue"] else None,
            })
    return {
        "patient": {k: p[k] for k in ("patient_code", "alias", "dob", "age", "sex", "notes")},
        "doctor_notes": notes,
        "recent_readings": recent_readings,
    }


def format_patient_context(patient, recent=3, db_path=DEFAULT_DB):
    """Render get_patient_context() as plain text to drop into an LLM prompt."""
    ctx = get_patient_context(patient, recent=recent, db_path=db_path)
    if ctx is None:
        return ""
    p = ctx["patient"]
    lines = [f"PATIENT CONTEXT — {p['patient_code']}"]
    demo = ", ".join(f"{k}={p[k]}" for k in ("alias", "age", "dob", "sex") if p.get(k))
    if demo:
        lines.append(demo)
    if p.get("notes"):
        lines.append(f"Profile: {p['notes']}")
    lines.append("")
    lines.append("Doctor's notes:")
    if ctx["doctor_notes"]:
        for n in ctx["doctor_notes"]:
            date = (n.get("note_date") or n.get("created_at") or "")[:10]
            cat = f" [{n['category']}]" if n.get("category") else ""
            who = f" — {n['author']}" if n.get("author") else ""
            lines.append(f"  • ({date}){cat} {n['note']}{who}")
    else:
        lines.append("  (none on file)")
    if ctx["recent_readings"]:
        lines.append("")
        lines.append("Recent readings (latest first):")
        for r in ctx["recent_readings"]:
            lines.append(f"  • {r['captured_at']}  HR={r['hr']}  "
                         f"voice_fatigue={r['fatigue_index']}  too_fatigued={r['too_fatigued']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- baselines ---
# Numeric features we baseline per patient (DB column -> used by fatigue_agent).
BASELINE_COLS = {
    "heart_features": ("hr", "hrv_rmssd", "hrv_sdnn", "hrv_breathingrate"),
    "voice_features": ("fatigue_index", "arousal_index", "pause_ratio",
                       "speech_rate_hz", "shimmer_pct"),
}


def get_baseline(patient_id, exclude_reading_id=None, min_n=3, db_path=DEFAULT_DB):
    """Per-patient mean/std for each feature, over the patient's *baseline*
    readings, so the agent can score personal deviations (z-scores) instead of
    absolute cutoffs.

    Baseline set: readings explicitly tagged is_baseline=1 if any exist,
    otherwise the patient's prior non-fatigued readings (too_fatigued not = 1).
    A feature is only returned when it has >= min_n samples with a non-zero std.

    Returns {"n_readings": int, "source": "tagged"|"non_fatigued", "features": {
        col: {"mean": .., "std": .., "n": ..}, ...}}.
    """
    with connect(db_path) as conn:
        tagged = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE patient_id = ? AND is_baseline = 1",
            (patient_id,),
        ).fetchone()[0]
        if tagged:
            where, params, source = "r.patient_id = ? AND r.is_baseline = 1", [patient_id], "tagged"
        else:
            where = "r.patient_id = ? AND COALESCE(d.too_fatigued, 0) = 0"
            params, source = [patient_id], "non_fatigued"
        if exclude_reading_id is not None:
            where += " AND r.id <> ?"
            params.append(exclude_reading_id)
        ids = [row["id"] for row in conn.execute(
            "SELECT r.id FROM readings r "
            "LEFT JOIN fatigue_decisions d ON d.reading_id = r.id "
            f"WHERE {where}", params).fetchall()]
        if not ids:
            return {"n_readings": 0, "source": source, "features": {}}

        qmarks = ",".join("?" * len(ids))
        feats = {}
        for table, cols in BASELINE_COLS.items():
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM {table} WHERE reading_id IN ({qmarks})",
                ids).fetchall()
            for c in cols:
                vals = [row[c] for row in rows if row[c] is not None]
                if len(vals) >= min_n:
                    sd = statistics.pstdev(vals)
                    if sd > 1e-6:
                        feats[c] = {"mean": round(statistics.fmean(vals), 4),
                                    "std": round(sd, 4), "n": len(vals)}
        return {"n_readings": len(ids), "source": source, "features": feats}


# ------------------------------------------------------------------- reads ---
def get_readings(patient_id, db_path=DEFAULT_DB):
    """All readings for a patient, oldest->newest, each reassembled to nested
    dicts mirroring the JSON shapes (heart/voice/fatigue legs, null if absent)."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM readings WHERE patient_id = ? ORDER BY captured_at ASC",
            (patient_id,),
        ).fetchall()
        return [_load_reading(conn, r["id"]) for r in rows]


def get_reading(reading_id, db_path=DEFAULT_DB):
    """A single reading reassembled to nested dicts (or None)."""
    with connect(db_path) as conn:
        return _load_reading(conn, reading_id)


def get_patient(patient_code, db_path=DEFAULT_DB):
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE patient_code = ?", (patient_code,)
        ).fetchone()
        return dict(row) if row else None


def _resolve_patient(conn, patient):
    """Look up a patient row by id (int) or patient_code (str). Returns dict/None."""
    col = "id" if isinstance(patient, int) else "patient_code"
    row = conn.execute(
        f"SELECT * FROM patients WHERE {col} = ?", (patient,)
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------- internals ---
def _write_heart(conn, rid, reading):
    hrv = reading.get("hrv") or {}
    conn.execute(
        "INSERT OR REPLACE INTO heart_features "
        "(reading_id, duration_s, fs, hr, sqi, hrv_sdnn, hrv_rmssd, hrv_pnn50, "
        " hrv_lf_hf, hrv_breathingrate, bvp_json, embedding_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid,
            reading.get("duration_s"), reading.get("fs"),
            reading.get("hr"), reading.get("sqi"),
            hrv.get("sdnn"), hrv.get("rmssd"), hrv.get("pnn50"),
            hrv.get("lf_hf"), hrv.get("breathingrate"),
            _dumps(reading.get("bvp")), _dumps(reading.get("embedding")),
        ),
    )


VOICE_COLS = (
    "duration_s", "sr", "f0_mean_hz", "f0_std_hz", "jitter_pct", "shimmer_pct",
    "voiced_ratio", "pause_ratio", "speech_rate_hz", "rms_mean", "rms_std",
    "spectral_centroid_hz", "arousal_index", "fatigue_index",
)


def _write_voice(conn, rid, voice):
    cols = ", ".join(("reading_id",) + VOICE_COLS)
    ph = ", ".join(["?"] * (1 + len(VOICE_COLS)))
    conn.execute(
        f"INSERT OR REPLACE INTO voice_features ({cols}) VALUES ({ph})",
        (rid, *(voice.get(c) for c in VOICE_COLS)),
    )


def _load_reading(conn, rid):
    r = conn.execute("SELECT * FROM readings WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    out = {
        "id": r["id"], "patient_id": r["patient_id"],
        "captured_at": r["captured_at"], "source": r["source"],
        "created_at": r["created_at"], "reading": None, "voice": None,
        "fatigue": None,
    }
    h = conn.execute("SELECT * FROM heart_features WHERE reading_id = ?", (rid,)).fetchone()
    if h:
        out["reading"] = {
            "source": r["source"], "captured_at": r["captured_at"],
            "duration_s": h["duration_s"], "fs": h["fs"],
            "hr": h["hr"], "sqi": h["sqi"],
            "hrv": {
                "sdnn": h["hrv_sdnn"], "rmssd": h["hrv_rmssd"],
                "pnn50": h["hrv_pnn50"], "lf_hf": h["hrv_lf_hf"],
                "breathingrate": h["hrv_breathingrate"],
            },
            "bvp": _loads(h["bvp_json"]),
            "embedding": _loads(h["embedding_json"]),
        }
    v = conn.execute("SELECT * FROM voice_features WHERE reading_id = ?", (rid,)).fetchone()
    if v:
        out["voice"] = {c: v[c] for c in VOICE_COLS}
    d = conn.execute("SELECT * FROM fatigue_decisions WHERE reading_id = ?", (rid,)).fetchone()
    if d:
        out["fatigue"] = {
            "too_fatigued": None if d["too_fatigued"] is None else bool(d["too_fatigued"]),
            "confidence": d["confidence"], "reasoning": d["reasoning"],
            "model": d["model"], "features": _loads(d["features_json"]),
            "created_at": d["created_at"],
        }
    return out


def _dumps(x):
    return None if x is None else json.dumps(x, separators=(",", ":"))


def _loads(s):
    return None if s is None else json.loads(s)


def _asbool(x):
    return None if x is None else int(bool(x))


# --------------------------------------------------------------------- cli ---
def _cli():
    ap = argparse.ArgumentParser(description="Vitals SQLite persistence.")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"DB path (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create tables")

    ing = sub.add_parser("ingest", help="ingest reading.json / voice_features.json / decision.json")
    ing.add_argument("--patient", required=True, help="opaque patient_code")
    ing.add_argument("--reading", help="path to reading.json (heart leg)")
    ing.add_argument("--voice", help="path to voice_features.json (voice leg)")
    ing.add_argument("--decision", help="path to decision.json (fatigue loop output)")
    ing.add_argument("--source", help="override source label")
    ing.add_argument("--baseline", action="store_true",
                     help="mark this as a rested/normal reference capture")
    ing.add_argument("--note", help="attach a doctor's note to the patient")
    ing.add_argument("--note-author", help="author for --note")

    note = sub.add_parser("note", help="add a doctor's note to a patient")
    note.add_argument("--patient", required=True, help="opaque patient_code")
    note.add_argument("--text", help="note body (or use --file)")
    note.add_argument("--file", help="read note body from a text file")
    note.add_argument("--author", help="clinician name/id")
    note.add_argument("--category", help="tag e.g. history/medication/instruction")
    note.add_argument("--date", help="clinical date ISO (default: now)")

    ctx = sub.add_parser("context", help="print patient context (demographics + notes + recent readings)")
    ctx.add_argument("--patient", required=True)
    ctx.add_argument("--recent", type=int, default=3, help="how many recent readings to show")

    bl = sub.add_parser("baseline", help="show a patient's computed per-feature baseline")
    bl.add_argument("--patient", required=True)
    bl.add_argument("--min-n", type=int, default=3, help="min samples per feature")

    lst = sub.add_parser("list", help="list a patient's readings")
    lst.add_argument("--patient", required=True)

    args = ap.parse_args()

    if args.cmd == "init":
        print(f"Initialized {init_db(args.db)}")
        return

    if args.cmd == "ingest":
        reading = _read_json(args.reading) if args.reading else None
        voice = _read_json(args.voice) if args.voice else None
        decision = _read_json(args.decision) if args.decision else None
        if reading is None and voice is None:
            ap.error("provide --reading and/or --voice")
        pid = insert_patient(args.patient, db_path=args.db)
        rid = insert_reading(pid, reading=reading, voice=voice,
                             source=args.source, is_baseline=args.baseline, db_path=args.db)
        legs = "+".join(x for x, on in (("heart", reading), ("voice", voice)) if on)
        if args.baseline:
            legs += " [baseline]"
        if decision is not None:
            insert_fatigue_decision(rid, _normalize_decision(decision), db_path=args.db)
            legs += "+fatigue"
        if args.note:
            insert_doctor_note(pid, args.note, author=args.note_author, db_path=args.db)
        print(f"patient={args.patient}(id={pid}) reading id={rid} legs={legs} -> {args.db}")
        return

    if args.cmd == "note":
        body = args.text
        if args.file:
            with open(args.file) as f:
                body = f.read().strip()
        if not body:
            ap.error("provide --text or --file")
        pid = insert_patient(args.patient, db_path=args.db)
        nid = insert_doctor_note(pid, body, author=args.author, category=args.category,
                                 note_date=args.date, db_path=args.db)
        print(f"patient={args.patient}(id={pid}) note id={nid} added -> {args.db}")
        return

    if args.cmd == "context":
        text = format_patient_context(args.patient, recent=args.recent, db_path=args.db)
        print(text or f"No patient {args.patient}")
        return

    if args.cmd == "baseline":
        p = get_patient(args.patient, db_path=args.db)
        if not p:
            print(f"No patient {args.patient}")
            return
        bl = get_baseline(p["id"], min_n=args.min_n, db_path=args.db)
        print(f"{args.patient} (id={p['id']}) baseline from {bl['n_readings']} "
              f"{bl['source']} reading(s):")
        if not bl["features"]:
            print(f"  (not enough data — need >= {args.min_n} samples per feature)")
        for c, s in bl["features"].items():
            print(f"  {c:20} mean={s['mean']}  std={s['std']}  n={s['n']}")
        return

    if args.cmd == "list":
        p = get_patient(args.patient, db_path=args.db)
        if not p:
            print(f"No patient {args.patient}")
            return
        rows = get_readings(p["id"], db_path=args.db)
        print(f"{args.patient} (id={p['id']}): {len(rows)} reading(s)")
        for r in rows:
            hr = r["reading"]["hr"] if r["reading"] else "—"
            fi = r["voice"]["fatigue_index"] if r["voice"] else "—"
            fat = r["fatigue"]["too_fatigued"] if r["fatigue"] else "—"
            print(f"  #{r['id']} {r['captured_at']}  HR={hr}  voice_fatigue={fi}  too_fatigued={fat}")


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _normalize_decision(d):
    """Map fatigue_agent.py's decision.json onto insert_fatigue_decision's shape.

    `engine` -> model; the whole decision dict is kept as the feature snapshot so
    the extra fields (fatigue_score, next_action, key_factors, inputs) aren't lost.
    """
    return {
        "too_fatigued": d.get("too_fatigued"),
        "confidence": d.get("confidence"),
        "reasoning": d.get("reasoning"),
        "model": d.get("model") or d.get("engine"),
        "features": d.get("features") or d,
    }


if __name__ == "__main__":
    _cli()
