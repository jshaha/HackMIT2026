"""
Keep voice analysis on the PATIENT only, robust to noise.

Two lightweight, dependency-free stages (numpy/scipy + the already-loaded
wav2vec2 model — no onnxruntime/webrtcvad, which segfault in this env):

  1. VAD  — an adaptive energy + spectral-flatness gate drops silence and
            steady background noise, returning speech-only audio.
  2. Speaker gate — enroll the patient once (a wav2vec2 voice embedding saved to
            patient_voice.npy); each incoming clip is cosine-matched to it, so
            other people talking (or noise that slips past VAD) is rejected.

Enrollment is optional: with no patient_voice.npy we still VAD-clean the audio.
"""
import os

import numpy as np
from scipy.signal import spectrogram

REF_PATH = "patient_voice.npy"
SPEAKER_THRESHOLD = 0.72   # cosine on wav2vec2 embeddings; below -> not patient


def vad_speech(sig, sr=16000, frame_ms=30, hop_ms=10):
    """Return (speech_only_signal, speech_seconds) using an adaptive gate.
    Noise floor is estimated from the quietest frames, so steady hum is rejected."""
    sig = np.asarray(sig, dtype=np.float64)
    flen, hop = int(sr * frame_ms / 1000), int(sr * hop_ms / 1000)
    if len(sig) < flen * 2:
        return sig.astype(np.float32), len(sig) / sr

    n = 1 + (len(sig) - flen) // hop
    idx = np.arange(flen)[None, :] + hop * np.arange(n)[:, None]
    frames = sig[idx]
    win = np.hanning(flen)

    # log-energy and spectral flatness per frame
    energy = np.log(np.mean((frames * win) ** 2, axis=1) + 1e-10)
    spec = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2 + 1e-12
    gmean = np.exp(np.mean(np.log(spec), axis=1))
    amean = np.mean(spec, axis=1)
    flatness = gmean / amean                      # ~1 = noise, low = voiced/tonal

    # adaptive energy threshold from the noise floor (10th percentile)
    floor = np.percentile(energy, 10)
    peak = np.percentile(energy, 95)
    thr = floor + 0.35 * (peak - floor)
    voiced = (energy > thr) & (flatness < 0.55)

    if not voiced.any():
        return np.zeros(0, dtype=np.float32), 0.0

    # reconstruct speech-only signal from voiced frames (with hop stride)
    keep = np.zeros(len(sig), dtype=bool)
    for i in np.flatnonzero(voiced):
        keep[i * hop: i * hop + flen] = True
    speech = sig[keep].astype(np.float32)
    return speech, len(speech) / sr


def enroll(sig, sr=16000, path=REF_PATH):
    """Compute + save the patient's voice embedding from an enrollment clip."""
    from voice_emotion import embed_speaker
    speech, sec = vad_speech(sig, sr)
    if sec < 2.0:
        raise SystemExit("Enrollment too short/quiet — need ~a few seconds of speech.")
    emb = embed_speaker(speech, sr)
    np.save(path, emb)
    return emb, sec


def load_ref(path=REF_PATH):
    return np.load(path) if os.path.exists(path) else None


def verify(sig, sr, ref):
    """Cosine similarity of this clip's speaker embedding to the enrolled ref."""
    from voice_emotion import embed_speaker
    e = embed_speaker(sig, sr)
    return float(np.dot(e, ref) / (np.linalg.norm(e) * np.linalg.norm(ref) + 1e-9))


def isolate(sig, sr=16000, ref_path=REF_PATH):
    """Full gate: VAD -> speech; optional speaker match. Returns
    (analysis_signal_or_None, info). None means 'reject this clip'."""
    speech, sec = vad_speech(sig, sr)
    info = {"speech_s": round(sec, 1), "speaker_similarity": None, "is_patient": None}
    if sec < 1.0:
        info["reason"] = "insufficient speech"
        return None, info

    ref = load_ref(ref_path)
    if ref is not None:
        sim = verify(speech, sr, ref)
        info["speaker_similarity"] = round(sim, 3)
        info["is_patient"] = sim >= SPEAKER_THRESHOLD
        if not info["is_patient"]:
            info["reason"] = f"not patient (sim {sim:.2f} < {SPEAKER_THRESHOLD})"
            return None, info
    return speech, info
