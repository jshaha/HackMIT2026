"""
visit_pipeline.py — live Veeva/EDC orchestrator for a contactless-vitals visit.

Every ~8-10s it runs ONE edc_autofill pass then ONE oversight pass for the
subject/visit, keeping the dashboard JSONs live off the current transcript +
reading:
  - edc_autofill.py  -> vitals-dashboard/public/edc_forms.json
  - oversight.py     -> vitals-dashboard/public/soa_state.json
                        vitals-dashboard/public/oversight_state.json

Both are invoked as subprocesses against the SAME python (conda env) running
this loop, reading vitals-dashboard/public/transcript.json (published by
conversation_tracker.py). Failures — including oversight.py not existing yet
(a sibling agent is still building it) — are caught and logged; the loop keeps
running. On Ctrl-C it does a final `oversight.py --report` pass to write the
post-visit monitoring_report.json.

ALCOA-C: edc_autofill only ever writes DRAFTs; this orchestrator never confirms.

CLI:
    conda run -n papagei_env python visit_pipeline.py --subject S-001 --visit V2 [--interval 8]
Stop with Ctrl-C.
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "vitals-dashboard", "public")
TRANSCRIPT = os.path.join(PUBLIC, "transcript.json")
EDC_AUTOFILL = os.path.join(HERE, "edc_autofill.py")
OVERSIGHT = os.path.join(HERE, "oversight.py")


def _run(script, args, label):
    """Run a pipeline stage as a subprocess. Returns (ok, note).

    Tolerates a missing script (esp. oversight.py, still being built) and any
    non-zero exit — never raises so the loop keeps going.
    """
    if not os.path.exists(script):
        return False, f"{label}: {os.path.basename(script)} not present yet (skipped)"
    try:
        proc = subprocess.run(
            [sys.executable, script, *args],
            cwd=HERE, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return False, f"{label}: exec error ({e})"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit {proc.returncode}"
        return False, f"{label}: failed ({detail})"
    return True, f"{label}: ok"


def run_cycle(subject, visit, transcript=TRANSCRIPT):
    """One edc_autofill pass then one oversight pass. Returns a status string.

    Never raises: each stage is independently tolerated so a broken/absent
    oversight.py cannot stop the EDC auto-fill from staying live.
    """
    common = ["--subject", subject, "--visit", visit,
              "--transcript", transcript]
    edc_ok, edc_note = _run(EDC_AUTOFILL, common, "edc_autofill")
    ov_ok, ov_note = _run(OVERSIGHT, common, "oversight")
    return f"{edc_note} | {ov_note}"


def final_report(subject, visit, transcript=TRANSCRIPT):
    """Final post-visit oversight pass with --report (writes monitoring_report)."""
    args = ["--subject", subject, "--visit", visit,
            "--transcript", transcript, "--report"]
    ok, note = _run(OVERSIGHT, args, "oversight --report")
    return note


def main():
    ap = argparse.ArgumentParser(
        description="Live EDC auto-fill + oversight orchestrator (Veeva layer).")
    ap.add_argument("--subject", default="S-001")
    ap.add_argument("--visit", default="V2")
    ap.add_argument("--interval", type=float, default=8.0,
                    help="seconds between cycles (~8-10s recommended)")
    ap.add_argument("--transcript", default=TRANSCRIPT)
    ap.add_argument("--once", action="store_true",
                    help="run a single cycle and exit (for testing)")
    args = ap.parse_args()

    print(f"[veeva] pipeline start — subject {args.subject}, visit {args.visit}, "
          f"every ~{args.interval:g}s. Ctrl-C to stop.", flush=True)

    if args.once:
        note = run_cycle(args.subject, args.visit, args.transcript)
        print(f"[veeva] cycle 1 · {note}", flush=True)
        return

    n = 0
    try:
        while True:
            n += 1
            note = run_cycle(args.subject, args.visit, args.transcript)
            print(f"[veeva] cycle {n} · {note}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[veeva] stopping — writing final monitoring report…", flush=True)
        try:
            note = final_report(args.subject, args.visit, args.transcript)
            print(f"[veeva] {note}", flush=True)
        except Exception as e:
            print(f"[veeva] final report failed: {e}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[veeva] stopped.")
