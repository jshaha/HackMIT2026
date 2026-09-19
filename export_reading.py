"""
Export a captured reading to JSON for the vitals-dashboard frontend.

Combines the BVP capture (bvp.npz / iphone_bvp.npz) with open-rppg's HR/HRV and
(optionally) a PaPaGei embedding, into one reading.json the dashboard loads via
its "Load reading" button.

Run in the 'rppg' env (needs open-rppg for HR/HRV):
    conda activate rppg
    python export_reading.py --in iphone_bvp.npz --out vitals-dashboard/public/reading.json
    # optional embedding (compute first with Stage 2, then pass it):
    #   python export_reading.py --in iphone_bvp.npz --embedding iphone_embeddings.npy ...
"""
import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import rppg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="iphone_bvp.npz")
    ap.add_argument("--embedding", default=None, help="optional embeddings.npy from Stage 2")
    ap.add_argument("--source", default="iPhone (Continuity)")
    ap.add_argument("--out", default="vitals-dashboard/public/reading.json")
    ap.add_argument("--max-bvp", type=int, default=1800, help="cap waveform points sent to UI")
    args = ap.parse_args()

    data = np.load(args.inp)
    bvp = np.asarray(data["bvp"], dtype=float)
    fs = float(data["fs"])
    dur = len(bvp) / fs

    # Recompute HR/HRV via open-rppg on the saved waveform.
    m = rppg.Model()
    hr = sqi = None
    hrv = {}
    try:
        m.set_signal(bvp, fs)  # if available in this build
    except Exception:
        pass
    try:
        res = m.hr_from_bvp(bvp, fs) if hasattr(m, "hr_from_bvp") else None
    except Exception:
        res = None
    if res:
        hr, sqi, hrv = res.get("hr"), res.get("SQI"), res.get("hrv") or {}

    # Fallback: FFT peak on the normalized waveform if open-rppg helper unavailable.
    if hr is None:
        w = bvp - bvp.mean()
        spec = np.abs(np.fft.rfft(w * np.hanning(len(w))))
        freqs = np.fft.rfftfreq(len(w), d=1 / fs)
        band = (freqs >= 0.7) & (freqs <= 3.0)  # 42–180 bpm
        hr = float(freqs[band][np.argmax(spec[band])] * 60)
        sqi = float(min(1.0, spec[band].max() / (spec.sum() + 1e-9) * 40))

    # Breathing rate from the BVP amplitude envelope (respiratory sinus
    # arrhythmia modulates pulse amplitude). Envelope -> PSD peak in the
    # 0.1–0.5 Hz band = 6–30 breaths/min.
    br = _breathing_rate(bvp, fs)

    # Downsample waveform for the UI (keep shape, cap points).
    if len(bvp) > args.max_bvp:
        idx = np.linspace(0, len(bvp) - 1, args.max_bvp).astype(int)
        wave = bvp[idx]
    else:
        wave = bvp
    wave = (wave / (np.max(np.abs(wave)) + 1e-9)).round(4).tolist()

    reading = {
        "source": args.source,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(dur, 1),
        "fs": fs,
        "hr": round(float(hr), 1),
        "sqi": round(float(sqi if sqi is not None else 0.0), 3),
        "br": _num(br),
        "hrv": {
            "sdnn": _num(hrv.get("sdnn")),
            "rmssd": _num(hrv.get("rmssd")),
            "pnn50": _num(hrv.get("pnn50")),
            "lf_hf": _num(hrv.get("LF/HF") or hrv.get("lf_hf")),
            "breathingrate": _num(hrv.get("breathingrate")) or _num(br),
        },
        "bvp": wave,
    }

    if args.embedding and os.path.exists(args.embedding):
        emb = np.load(args.embedding)
        reading["embedding"] = {
            "n_segments": int(emb.shape[0]),
            "dim": int(emb.shape[1]),
            "mean": emb.mean(0).round(4).tolist(),
        }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(reading, f)
    print(f"Wrote {args.out}: HR={reading['hr']} BR={reading['br']} "
          f"SQI={reading['sqi']} ({reading['duration_s']}s @ {fs:.0f}Hz, {len(wave)} pts)")


def _breathing_rate(bvp, fs):
    """Estimate breaths/min from BVP amplitude-envelope PSD, or None if the
    clip is too short / respiratory peak too weak to trust."""
    try:
        from scipy.signal import hilbert, welch
        x = np.asarray(bvp, dtype=float)
        if len(x) < fs * 15:  # need ~15s for a stable resp estimate
            return None
        env = np.abs(hilbert(x - x.mean()))
        env = env - env.mean()
        nper = int(min(len(env), fs * 20))
        f, p = welch(env, fs=fs, nperseg=nper)
        band = (f >= 0.1) & (f <= 0.5)  # 6–30 brpm
        if not band.any() or p[band].max() <= 0:
            return None
        return float(f[band][np.argmax(p[band])] * 60.0)
    except Exception:
        return None


def _num(x):
    try:
        return None if x is None else round(float(x), 2)
    except Exception:
        return None


if __name__ == "__main__":
    main()
