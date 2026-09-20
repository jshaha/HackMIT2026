"""
veeva_mock.py — mock Veeva Vault CDMS client (DEMO/MOCK).

Mirrors Vault REST/CDMS semantics: study -> casebook -> event/visit -> form -> item.
Backed by committed JSON DEFINITIONS under `veeva/`:
  - veeva/protocol_soa.json   (study + visits + activities = Schedule of Activities)
  - veeva/crf_forms.json      (eCRF form definitions / fields)
  - veeva/subjects.json       (enrolled subjects)
Mutable EDC state (field values + provenance + e-signatures + deviations) persists
to veeva/edc_store.json (runtime — do NOT commit).

ALCOA-C field lifecycle:
  empty  (no value)
    -> draft     (auto-filled/entered, with provenance: source/captured_at/confidence)
    -> confirmed (clinician e-sign via confirm_form: stamps signer + signed_at)

Only the clinician confirm step marks data confirmed. Auto-filled data is always DRAFT.

This module NEVER raises on a missing subject/visit — instances are created lazily
from the form definitions on first access.

Run smoke test:  conda run -n papagei_env python veeva_mock.py
"""

import json
import os
import time
from datetime import datetime, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


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


class VaultCDMS:
    """Mock Vault CDMS client. Instantiate with the definitions root dir.

    All mutable EDC state is persisted to <root>/edc_store.json after every
    mutation so concurrent processes (autofill loop, oversight, dashboard) see a
    consistent picture.
    """

    def __init__(self, root="veeva"):
        self.root = root
        self.soa_path = os.path.join(root, "protocol_soa.json")
        self.forms_path = os.path.join(root, "crf_forms.json")
        self.subjects_path = os.path.join(root, "subjects.json")
        self.store_path = os.path.join(root, "edc_store.json")

        self._soa = _read_json(self.soa_path, {}) or {}
        self._formdefs = _read_json(self.forms_path, {}) or {}
        self._subjects = _read_json(self.subjects_path, {}) or {}

        # form defs keyed by id for O(1) lookup
        self._formdef_by_id = {f["id"]: f for f in self._formdefs.get("forms", [])}

        self._store = _read_json(self.store_path) or self._empty_store()

    # ---------- persistence ----------

    def _empty_store(self):
        return {"forms": {}, "deviations": [], "_deviation_seq": 0}

    def _persist(self):
        _atomic_write(self.store_path, self._store)

    def _form_key(self, subject, visit, form_id):
        return f"{subject}|{visit}|{form_id}"

    # ---------- study / visits / SoA ----------

    def get_study(self):
        return dict(self._soa.get("study", {}))

    def list_visits(self):
        return [dict(v) for v in self._soa.get("visits", [])]

    def get_soa(self, visit_id=None):
        """Return the activities required at `visit_id` (all activities if None)."""
        acts = self._soa.get("activities", [])
        out = []
        for a in acts:
            if visit_id is None or visit_id in a.get("visits", []):
                out.append(dict(a))
        return out

    def _visit_ids(self):
        return {v["id"] for v in self._soa.get("visits", [])}

    def get_visit(self, visit_id):
        for v in self._soa.get("visits", []):
            if v["id"] == visit_id:
                return dict(v)
        return None

    # ---------- form definitions ----------

    def get_form_def(self, form_id):
        return dict(self._formdef_by_id.get(form_id, {}))

    def forms_at_visit(self, visit_id):
        """Form ids for the activities scheduled at this visit (dedup, ordered)."""
        seen, out = set(), []
        for a in self.get_soa(visit_id):
            fid = a.get("form")
            if fid and fid not in seen:
                seen.add(fid)
                out.append(fid)
        return out

    # ---------- form instances ----------

    def _new_field(self, fdef):
        return {
            "id": fdef["id"],
            "label": fdef.get("label", fdef["id"]),
            "type": fdef.get("type", "text"),
            "value": None,
            "status": "empty",
            "source": None,
            "captured_at": None,
            "confidence": None,
            "signer": None,
            "signed_at": None,
        }

    def _new_instance(self, subject, visit, form_id):
        fdef = self._formdef_by_id.get(form_id)
        name = fdef.get("name", form_id) if fdef else form_id
        fields = [self._new_field(fd) for fd in (fdef.get("fields", []) if fdef else [])]
        return {
            "form": form_id,
            "name": name,
            "subject": subject,
            "visit": visit,
            "status": "empty",
            "fields": fields,
        }

    def _get_or_create_instance(self, subject, visit, form_id):
        key = self._form_key(subject, visit, form_id)
        inst = self._store["forms"].get(key)
        if inst is None:
            inst = self._new_instance(subject, visit, form_id)
            self._store["forms"][key] = inst
        return inst

    def _recompute_form_status(self, inst):
        statuses = [f["status"] for f in inst["fields"]]
        if statuses and all(s == "confirmed" for s in statuses if s != "empty") and \
                any(s == "confirmed" for s in statuses):
            # at least one confirmed and no drafts remaining
            if any(s == "draft" for s in statuses):
                inst["status"] = "draft"
            else:
                inst["status"] = "confirmed"
        elif any(s == "draft" for s in statuses):
            inst["status"] = "draft"
        elif any(s == "confirmed" for s in statuses):
            inst["status"] = "confirmed"
        else:
            inst["status"] = "empty"
        return inst

    def get_forms(self, subject, visit):
        """Return all form instances required at `visit` for `subject`.

        Creates empty instances from the definitions on first call so the caller
        (and the dashboard) can see what still needs data. Never raises on an
        unknown subject/visit.
        """
        form_ids = self.forms_at_visit(visit)
        if not form_ids:
            # unknown/lazily-created visit: fall back to every defined form so we
            # still return something sensible rather than raising.
            form_ids = list(self._formdef_by_id.keys())
        out = []
        changed = False
        for fid in form_ids:
            key = self._form_key(subject, visit, fid)
            if key not in self._store["forms"]:
                changed = True
            out.append(self._get_or_create_instance(subject, visit, fid))
        if changed:
            self._persist()
        return [json.loads(json.dumps(i)) for i in out]

    def get_form(self, subject, visit, form_id):
        """Return a single form instance (created lazily if absent)."""
        existed = self._form_key(subject, visit, form_id) in self._store["forms"]
        inst = self._get_or_create_instance(subject, visit, form_id)
        if not existed:
            self._persist()
        return json.loads(json.dumps(inst))

    def submit_form_data(self, subject, visit, form_id, fields, status="draft",
                         source=None, confidence=None):
        """Upsert field values with provenance. Sets field.status (default draft).

        `fields` is a dict of field_id -> value. Only fields present in the form
        definition are written; unknown ids are ignored. Provenance
        (source/captured_at/confidence) is stamped on each written field for ALCOA
        attributability. Returns the updated form_instance.
        """
        inst = self._get_or_create_instance(subject, visit, form_id)
        by_id = {f["id"]: f for f in inst["fields"]}
        now = _now_iso()
        for fid, value in (fields or {}).items():
            f = by_id.get(fid)
            if f is None:
                # field not in the def — skip silently (never raise)
                continue
            if value is None:
                continue
            f["value"] = value
            f["status"] = status
            f["source"] = source
            f["captured_at"] = now
            f["confidence"] = confidence
            # a fresh submission resets any prior signature
            if status != "confirmed":
                f["signer"] = None
                f["signed_at"] = None
        self._recompute_form_status(inst)
        self._persist()
        return json.loads(json.dumps(inst))

    def confirm_form(self, subject, visit, form_id, signer="clinician"):
        """Clinician e-sign: flip every draft field to confirmed, stamping the
        signer + time (ALCOA-C attributable, contemporaneous). Fields still empty
        are left empty. Returns the updated form_instance.
        """
        inst = self._get_or_create_instance(subject, visit, form_id)
        now = _now_iso()
        for f in inst["fields"]:
            if f["status"] == "draft":
                f["status"] = "confirmed"
                f["signer"] = signer
                f["signed_at"] = now
        self._recompute_form_status(inst)
        self._persist()
        return json.loads(json.dumps(inst))

    # ---------- deviations ----------

    def record_deviation(self, subject, visit, activity_id, severity, description):
        self._store["_deviation_seq"] += 1
        dev = {
            "id": self._store["_deviation_seq"],
            "subject": subject,
            "visit": visit,
            "activity": activity_id,
            "severity": severity,
            "description": description,
            "recorded_at": _now_iso(),
        }
        self._store["deviations"].append(dev)
        self._persist()
        return dict(dev)

    def list_deviations(self, subject, visit):
        return [dict(d) for d in self._store["deviations"]
                if d["subject"] == subject and d["visit"] == visit]

    # ---------- windows ----------

    def visit_in_window(self, visit_id, day_offset=None):
        """True if day_offset (days from Day 1) falls in the visit's window.

        For the demo, a missing day_offset is treated as in-window (True) so the
        pipeline stays green without a real calendar. Unknown visits default True.
        """
        if day_offset is None:
            return True
        v = self.get_visit(visit_id)
        if not v:
            return True
        lo, hi = v.get("window_days", [None, None])
        if lo is None or hi is None:
            return True
        return lo <= day_offset <= hi


def _smoke():
    print("=== VaultCDMS smoke test (DEMO/MOCK) ===")
    cdms = VaultCDMS(root="veeva")
    print("study:", cdms.get_study().get("id"))
    print("visits:", [v["id"] for v in cdms.list_visits()])
    soa_v2 = cdms.get_soa("V2")
    print("SoA @ V2 (required forms):",
          [(a["id"], a["form"], a["required"]) for a in soa_v2])
    assert {a["form"] for a in soa_v2} >= {"VS", "AE", "CM", "PRO"}, "V2 missing forms"

    # round-trip: submit draft with provenance
    inst = cdms.submit_form_data(
        "S-001", "V2", "VS",
        {"hr": 71, "resp_rate": 13, "vs_method": "Contactless rPPG (camera)"},
        status="draft", source="contactless-rppg", confidence=0.587)
    hr = next(f for f in inst["fields"] if f["id"] == "hr")
    assert inst["status"] == "draft" and hr["value"] == 71 and hr["status"] == "draft"
    assert hr["source"] == "contactless-rppg" and hr["confidence"] == 0.587
    assert hr["captured_at"] and hr["signer"] is None
    print("submit -> VS status:", inst["status"], "| hr draft w/ provenance OK")

    got = cdms.get_form("S-001", "V2", "VS")
    hr2 = next(f for f in got["fields"] if f["id"] == "hr")
    assert hr2["status"] == "draft" and hr2["value"] == 71
    print("get_form shows draft hr w/ provenance OK")

    # confirm -> e-sign
    conf = cdms.confirm_form("S-001", "V2", "VS", signer="Dr. Demo")
    hr3 = next(f for f in conf["fields"] if f["id"] == "hr")
    assert hr3["status"] == "confirmed" and hr3["signer"] == "Dr. Demo" and hr3["signed_at"]
    print("confirm_form -> hr confirmed by", hr3["signer"], "| form status:", conf["status"])

    # get_forms creates empty instances for all V2 forms
    allforms = cdms.get_forms("S-001", "V2")
    print("get_forms @ V2:", [(f["form"], f["status"]) for f in allforms])
    assert {f["form"] for f in allforms} >= {"VS", "AE", "CM", "PRO"}

    # lazy subject/visit (never raise)
    lazy = cdms.get_forms("S-999", "V3")
    print("lazy subject S-999/V3 ok, forms:", [f["form"] for f in lazy])

    # deviations
    dev = cdms.record_deviation("S-001", "V2", "fatigue_pro", "minor",
                                "Required Fatigue PRO not captured")
    print("deviation recorded id:", dev["id"])
    assert cdms.list_deviations("S-001", "V2")[0]["id"] == dev["id"]

    # windows
    assert cdms.visit_in_window("V2", 1) is True
    assert cdms.visit_in_window("V2", 10) is False
    assert cdms.visit_in_window("V2") is True
    print("visit_in_window OK")
    print("=== smoke test PASSED ===")


if __name__ == "__main__":
    _smoke()
