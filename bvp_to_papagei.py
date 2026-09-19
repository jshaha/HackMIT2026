"""
Stage 2 of the vitals pipeline: BVP waveform -> PaPaGei health-feature embedding.

Run in the 'papagei_env' conda env, from inside the PaPaGei repo so its
modules import (or this script adds the repo to sys.path automatically):

    conda activate papagei_env
    python bvp_to_papagei.py --in bvp.npz

Input : bvp.npz  (from capture_bvp.py) with arrays  bvp, fs
Output: embeddings.npy  -> shape (num_segments, 512), plus a mean 512-d vector.

PaPaGei recipe: z-score normalize -> Chebyshev bandpass (preprocess_one_ppg_signal)
-> resample to 125 Hz -> segment into 1250-sample (10s) windows -> ResNet1DMoE.
"""
import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import torch

# Make the PaPaGei repo importable regardless of where this script sits.
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "papagei-foundation-model")
if os.path.isdir(REPO) and REPO not in sys.path:
    sys.path.insert(0, REPO)

from preprocessing.ppg import preprocess_one_ppg_signal
from segmentations import waveform_to_segments
from linearprobing.utils import resample_batch_signal, load_model_without_module_prefix
from models.resnet import ResNet1DMoE
from torch_ecg._preprocessors import Normalize

FS_TARGET = 125
SEG_LEN = 1250  # 10 s @ 125 Hz -- PaPaGei's expected input length


def load_papagei(weights_path, device):
    model = ResNet1DMoE(in_channels=1, base_filters=32, kernel_size=3, stride=2,
                        groups=1, n_block=18, n_classes=512, n_experts=3)
    model = load_model_without_module_prefix(model, weights_path)
    return model.to(device).eval()


def bvp_to_segments(bvp, fs):
    """Filtered + resampled + segmented (num_segments, 1250), each z-scored."""
    norm = Normalize(method="z-score")
    sig, _ = norm.apply(np.asarray(bvp, dtype=np.float64), fs=fs)
    sig, _, _, _ = preprocess_one_ppg_signal(waveform=sig, frequency=fs)
    sig = resample_batch_signal(sig, fs_original=fs, fs_target=FS_TARGET, axis=0)

    seg_dict = waveform_to_segments("ppg", SEG_LEN, dict_data={"ppg": sig})
    segments = seg_dict["ppg"]
    if segments.ndim == 1 or len(segments) == 0:
        raise SystemExit(
            f"Signal too short: got {len(sig)} samples @125Hz, need >= {SEG_LEN} "
            f"(>=10s). Capture a longer recording.")
    # z-score each 10s segment
    out = np.vstack([norm.apply(s, fs=FS_TARGET)[0] for s in segments])
    return out.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="bvp.npz")
    ap.add_argument("--weights", default=os.path.join(REPO, "weights", "papagei_s.pt"))
    ap.add_argument("--out", default="embeddings.npy")
    args = ap.parse_args()

    data = np.load(args.inp)
    bvp, fs = data["bvp"], float(data["fs"])
    print(f"Loaded {bvp.shape[0]} samples @ {fs:.2f} Hz (~{bvp.shape[0]/fs:.1f}s)")

    segments = bvp_to_segments(bvp, fs)
    print(f"Prepared {segments.shape[0]} x {segments.shape[1]}-sample segments")

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = load_papagei(args.weights, device)

    x = torch.from_numpy(segments).unsqueeze(1).to(device)  # (N, 1, 1250)
    with torch.no_grad():
        out = model(x)
    emb = (out[0] if isinstance(out, tuple) else out).cpu().numpy()  # (N, 512)

    np.save(args.out, emb)
    print(f"Device: {device}")
    print(f"Embeddings: {emb.shape} -> saved {args.out}")
    print(f"Mean 512-d feature vector (first 8 dims): {emb.mean(0)[:8]}")


if __name__ == "__main__":
    main()
