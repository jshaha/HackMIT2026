"""
Enroll the patient's voice once, so live analysis stays on THEM.

Records nothing itself — pass a wav (enroll_voice.sh records it). Computes a
wav2vec2 voice embedding from the speech and saves it to patient_voice.npy.
Thereafter voice_decoder rejects clips that don't match this speaker.

    conda activate papagei_env
    python enroll_patient.py --in enroll.wav
"""
import argparse

import numpy as np
import soundfile as sf

from voice_isolation import enroll, REF_PATH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="enroll.wav")
    ap.add_argument("--out", default=REF_PATH)
    args = ap.parse_args()

    sig, sr = sf.read(args.inp)
    if sig.ndim > 1:
        sig = sig.mean(axis=1)
    emb, sec = enroll(np.asarray(sig, dtype=np.float64), sr, path=args.out)
    print(f"✅ Enrolled patient from {sec:.1f}s of speech "
          f"-> {args.out} ({emb.shape[0]}-d voice print).")
    print("   Live voice analysis will now only accept this speaker.")


if __name__ == "__main__":
    main()
