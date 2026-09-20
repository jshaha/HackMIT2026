"""
Voice-decoder leg of the vitals pipeline: turn a spoken-voice clip into
grounded acoustic markers of emotional state + fatigue.

These are literature-backed vocal correlates (no training data required):
  - F0 (pitch) mean / variability .... arousal, expressiveness
  - jitter / shimmer ................. vocal-fold stability (fatigue, strain)
  - voiced / pause ratio ............. speech continuity (fatigue -> more pauses)
  - speech rate (syllable proxy) ..... slows with fatigue
  - RMS energy mean / variability .... loudness, engagement
  - spectral centroid / tilt ......... vocal "brightness" (drops with fatigue)

From those we derive a transparent arousal_index and fatigue_index in [0,1].
The agentic loop consumes these numbers; it does NOT trust them blindly.

Run in the 'papagei_env' env (numpy + scipy + soundfile):
    conda activate papagei_env
    python voice_decoder.py --in voice.wav --out voice_features.json
"""
import argparse
import json

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks


def _frame(sig, flen, hop):
    n = 1 + max(0, (len(sig) - flen) // hop)
    idx = np.arange(flen)[None, :] + hop * np.arange(n)[:, None]
    return sig[idx] if n > 0 else np.empty((0, flen))


def _f0_autocorr(frame, sr, fmin=70.0, fmax=350.0):
    """Fundamental frequency of one frame via autocorrelation; 0 if unvoiced."""
    f = frame - frame.mean()
    if np.sqrt(np.mean(f ** 2)) < 1e-4:
        return 0.0
    corr = np.correlate(f, f, mode="full")[len(f) - 1:]
    lo, hi = int(sr / fmax), int(sr / fmin)
    if hi >= len(corr):
        return 0.0
    seg = corr[lo:hi]
    if seg.size == 0 or corr[0] <= 0:
        return 0.0
    lag = lo + int(np.argmax(seg))
    # require a reasonably periodic peak
    if corr[lag] / corr[0] < 0.3:
        return 0.0
    return float(sr / lag)


def extract(wav_path, isolate=True):
    sig, sr = sf.read(wav_path)
    if sig.ndim > 1:
        sig = sig.mean(axis=1)
    sig = sig.astype(np.float64)

    # Keep analysis on the patient's voice: VAD-clean noise/silence, and (if a
    # patient is enrolled) reject clips that aren't them. Degrades gracefully.
    iso_info = {}
    if isolate:
        try:
            from voice_isolation import isolate as _isolate
            speech, iso_info = _isolate(sig, sr)
            if speech is None:
                raise SystemExit(iso_info.get("reason", "rejected by voice isolation"))
            sig = np.asarray(speech, dtype=np.float64)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[voice] isolation unavailable ({e}); analyzing raw audio.")

    dur = len(sig) / sr
    flen, hop = int(0.025 * sr), int(0.010 * sr)  # 25 ms / 10 ms
    frames = _frame(sig, flen, hop)
    if len(frames) < 5:
        raise SystemExit("Clip too short / silent. Record a few seconds of speech.")

    win = np.hanning(flen)
    rms = np.sqrt(np.mean((frames * win) ** 2, axis=1) + 1e-12)
    rms_norm = rms / (rms.max() + 1e-12)

    # voiced/silent gating from energy envelope
    voiced_mask = rms_norm > 0.15
    voiced_ratio = float(voiced_mask.mean())
    pause_ratio = float((rms_norm < 0.08).mean())

    # per-frame F0 over voiced frames
    f0 = np.array([_f0_autocorr(fr, sr) if v else 0.0
                   for fr, v in zip(frames, voiced_mask)])
    f0v = f0[f0 > 0]
    f0_mean = float(f0v.mean()) if f0v.size else 0.0
    f0_std = float(f0v.std()) if f0v.size > 1 else 0.0

    # jitter: cycle-to-cycle period variation (%); shimmer: amplitude variation (%)
    if f0v.size > 2:
        periods = 1.0 / f0v
        jitter = float(np.mean(np.abs(np.diff(periods))) / (periods.mean() + 1e-12) * 100)
    else:
        jitter = 0.0
    amps = rms[voiced_mask]
    if amps.size > 2:
        shimmer = float(np.mean(np.abs(np.diff(amps))) / (amps.mean() + 1e-12) * 100)
    else:
        shimmer = 0.0

    # speech rate: syllable-nucleus proxy = energy-envelope peaks per second
    env = rms_norm
    peaks, _ = find_peaks(env, height=0.2, distance=int(0.12 / 0.010))  # >=120 ms apart
    speech_rate = float(len(peaks) / dur) if dur > 0 else 0.0

    # spectral centroid (brightness) averaged over voiced frames
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(flen, d=1 / sr)
    cen = (spec * freqs).sum(axis=1) / (spec.sum(axis=1) + 1e-12)
    centroid = float(cen[voiced_mask].mean()) if voiced_mask.any() else float(cen.mean())

    rms_mean = float(rms.mean())
    rms_std = float(rms.std())

    feats = {
        "duration_s": round(dur, 1),
        "sr": sr,
        "f0_mean_hz": round(f0_mean, 1),
        "f0_std_hz": round(f0_std, 1),
        "jitter_pct": round(jitter, 2),
        "shimmer_pct": round(shimmer, 2),
        "voiced_ratio": round(voiced_ratio, 3),
        "pause_ratio": round(pause_ratio, 3),
        "speech_rate_hz": round(speech_rate, 2),
        "rms_mean": round(rms_mean, 4),
        "rms_std": round(rms_std, 4),
        "spectral_centroid_hz": round(centroid, 1),
    }
    # Dimensional emotion from the pretrained wav2vec2 SER model (arousal /
    # valence / dominance in [0,1]); falls back to an acoustic proxy if the
    # model can't load. Low arousal is the primary fatigue signal.
    emo, src = _emotion(sig, sr, feats)
    feats["arousal_index"] = round(emo["arousal"], 3)
    feats["valence"] = round(emo["valence"], 3)
    feats["dominance"] = round(emo["dominance"], 3)
    feats["emotion_source"] = src
    feats["emotional_state"] = _state_label(emo["arousal"], emo["valence"])
    feats["fatigue_index"] = round(_fatigue(feats), 3)
    if iso_info:
        feats["speech_s"] = iso_info.get("speech_s")
        feats["speaker_similarity"] = iso_info.get("speaker_similarity")
        feats["is_patient"] = iso_info.get("is_patient")
    return feats


def _clip01(x):
    return float(min(1.0, max(0.0, x)))


def _emotion(sig, sr, f):
    """Pretrained SER (arousal/valence/dominance) with an acoustic fallback."""
    try:
        from voice_emotion import predict
        return predict(sig, sr), "wav2vec2-msp-dim"
    except Exception as e:
        print(f"[voice] SER model unavailable ({e}); using acoustic proxy.")
        return {"arousal": _arousal_acoustic(f), "valence": 0.5,
                "dominance": 0.5}, "acoustic-proxy"


def _arousal_acoustic(f):
    """Fallback only: higher pitch variability, brighter/louder/faster speech
    -> more aroused. Used when the SER model can't load."""
    return _clip01(
        0.30 * _clip01(f["f0_std_hz"] / 45.0) +
        0.25 * _clip01(f["spectral_centroid_hz"] / 2500.0) +
        0.20 * _clip01(f["rms_std"] / 0.10) +
        0.25 * _clip01(f["speech_rate_hz"] / 4.5)
    )


def _state_label(arousal, valence):
    """Map the arousal/valence plane to a coarse emotional-state label."""
    hi_a, lo_a = arousal >= 0.55, arousal < 0.45
    hi_v, lo_v = valence >= 0.55, valence < 0.45
    if lo_a and lo_v:
        return "fatigued / low"
    if lo_a and hi_v:
        return "calm / relaxed"
    if hi_a and hi_v:
        return "engaged / positive"
    if hi_a and lo_v:
        return "agitated / stressed"
    if lo_a:
        return "low energy"
    if hi_a:
        return "activated"
    return "neutral"


def _fatigue(f):
    """Fatigue driven primarily by LOW model arousal, with acoustic context
    (pauses, slow speech, vocal instability). All transparent."""
    return _clip01(
        0.50 * (1 - f["arousal_index"]) +
        0.20 * _clip01(f["pause_ratio"] / 0.5) +
        0.15 * _clip01(1 - f["speech_rate_hz"] / 4.5) +
        0.15 * _clip01(f["shimmer_pct"] / 12.0)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="voice.wav")
    ap.add_argument("--out", default="voice_features.json")
    args = ap.parse_args()

    feats = extract(args.inp)
    with open(args.out, "w") as fp:
        json.dump(feats, fp, indent=2)

    f = feats
    print("")
    print("┌─ Voice decoder " + "─" * 32)
    print(f"│ Duration    : {f['duration_s']}s @ {f['sr']} Hz")
    print(f"│ Pitch F0    : {f['f0_mean_hz']} Hz  (±{f['f0_std_hz']} variability)")
    print(f"│ Jitter/Shim : {f['jitter_pct']}% / {f['shimmer_pct']}%")
    print(f"│ Voiced/Pause: {f['voiced_ratio']} / {f['pause_ratio']}")
    print(f"│ Speech rate : {f['speech_rate_hz']} syl/s")
    print(f"│ Loudness    : {f['rms_mean']} (±{f['rms_std']})")
    print(f"│ Brightness  : {f['spectral_centroid_hz']} Hz")
    print(f"│ ── emotion ({f['emotion_source']}) ──")
    print(f"│ Arousal     : {f['arousal_index']}   (low = fatigued)")
    print(f"│ Valence     : {f['valence']}")
    print(f"│ Dominance   : {f['dominance']}")
    print(f"│ State       : {f['emotional_state']}")
    print(f"│ Fatigue idx : {f['fatigue_index']}")
    print("└" + "─" * 48)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
