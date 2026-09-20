"""
Lightweight facial-expression -> emotional-state extractor for the video leg.

Uses the FER+ (emotion-ferplus) ONNX model from the ONNX Model Zoo — a small
(~35 MB) CNN that classifies a grayscale face crop into 8 emotions:
    neutral, happiness, surprise, sadness, anger, disgust, fear, contempt.

It runs on onnxruntime (CPUExecutionProvider), so it needs NO torch — matching
the 'rppg' conda env that open-rppg already uses for BlazeFace. The model is
downloaded once via urllib and cached under ./models/ in the repo.

We turn the 8 discrete emotions into a 2-D affect estimate (arousal / valence)
via a documented circumplex mapping, because the fatigue agent thinks in those
terms. Low arousal + negative valence (sad / flat / tired-looking) is the
fatigue-relevant signal.

    from face_emotion import predict
    predict(frame_rgb, box=((y1,y2),(x1,x2)))  # full frame + face box
    predict(face_crop)                          # already-cropped face

Returns None (never raises) if the model can't be downloaded or loaded.

Test on an image:
    conda run -n rppg python face_emotion.py path/to/face.jpg
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(HERE, "models")
MODEL_PATH = os.path.join(MODELS_DIR, "emotion-ferplus-8.onnx")

# FER+ ONNX model, opset 8, from the official ONNX Model Zoo (validated release).
MODEL_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/"
    "body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx"
)

# FER+ output order (fixed by the trained model).
LABELS = ["neutral", "happiness", "surprise", "sadness",
          "anger", "disgust", "fear", "contempt"]

# --- Circumplex (Russell) affect mapping ---------------------------------
# Each emotion is placed on the arousal/valence plane in [0,1]:
#   arousal  : 0 = calm / sleepy / low-energy ...... 1 = alert / activated
#   valence  : 0 = negative / unpleasant ........... 1 = positive / pleasant
# Placement (typical circumplex coordinates, normalized to 0..1):
#   neutral   -> moderate-low arousal, neutral valence
#   happiness -> moderate-high arousal, high valence
#   surprise  -> high arousal, slightly positive valence
#   sadness   -> LOW arousal, negative valence   (fatigue-relevant)
#   anger     -> high arousal, negative valence
#   disgust   -> moderate arousal, negative valence
#   fear      -> high arousal, negative valence
#   contempt  -> moderate-low arousal, slightly negative valence
# The frame estimate is the probability-weighted average over the 8 emotions,
# so a flat/sad face pulls arousal down and valence down together.
AV_MAP = {
    "neutral":   (0.35, 0.50),
    "happiness": (0.65, 0.90),
    "surprise":  (0.90, 0.60),
    "sadness":   (0.20, 0.20),
    "anger":     (0.85, 0.15),
    "disgust":   (0.55, 0.20),
    "fear":      (0.90, 0.15),
    "contempt":  (0.40, 0.35),
}

SOURCE = "fer-ferplus"

_session = None
_load_failed = False


def _ensure_model():
    """Download the FER+ ONNX model to ./models/ if missing. Returns True on success."""
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 1_000_000:
        return True
    try:
        import urllib.request
        os.makedirs(MODELS_DIR, exist_ok=True)
        print(f"[face_emotion] downloading FER+ model -> {MODEL_PATH} ...", flush=True)
        tmp = MODEL_PATH + ".tmp"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        if os.path.getsize(tmp) < 1_000_000:
            os.remove(tmp)
            return False
        os.replace(tmp, MODEL_PATH)
        return True
    except Exception as e:
        print(f"[face_emotion] WARNING: could not download model: {e!r}", flush=True)
        return False


def load_model():
    """Lazily create and cache the onnxruntime session. Returns the session or None."""
    global _session, _load_failed
    if _session is not None:
        return _session
    if _load_failed:
        return None
    try:
        if not _ensure_model():
            _load_failed = True
            return None
        import onnxruntime as ort
        _session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
        return _session
    except Exception as e:
        print(f"[face_emotion] WARNING: could not load FER+ model: {e!r}", flush=True)
        _load_failed = True
        return None


def _softmax(x):
    x = x - np.max(x)
    e = np.exp(x)
    return e / (np.sum(e) + 1e-9)


def _to_gray(img):
    """Return a single-channel uint8 image from a (H,W), (H,W,1) or (H,W,3) array.
    Assumes 3-channel input is RGB (open-rppg's convention)."""
    img = np.asarray(img)
    if img.ndim == 2:
        return img
    if img.ndim == 3:
        if img.shape[2] == 1:
            return img[:, :, 0]
        # RGB -> luma (Rec.601)
        return (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1]
                + 0.114 * img[:, :, 2])
    raise ValueError(f"unexpected image shape {img.shape}")


def _preprocess(face):
    """FER+ expects a 64x64 grayscale float32 tensor of shape (1,1,64,64)."""
    import cv2
    gray = _to_gray(face)
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
    x = gray.astype(np.float32)
    return x.reshape(1, 1, 64, 64)


def _crop(frame, box):
    """Crop a face from a full frame given box = ((y1,y2),(x1,x2))."""
    (y1, y2), (x1, x2) = box[0], box[1]
    h, w = frame.shape[:2]
    y1 = max(0, int(y1)); y2 = min(h, int(y2))
    x1 = max(0, int(x1)); x2 = min(w, int(x2))
    if y2 <= y1 or x2 <= x1:
        return None
    return frame[y1:y2, x1:x2]


_FRIENDLY = {
    "happiness": "happy / positive", "sadness": "sad / low",
    "anger": "angry / tense", "fear": "anxious", "surprise": "surprised",
    "disgust": "displeased", "contempt": "disengaged",
}


def _state_label(arousal, valence, prob_map):
    """A responsive emotional-state label. FER+ is heavily neutral-biased, so:
    name a clearly-expressed emotion when one stands out, otherwise place the
    face on the arousal/valence plane (graded — 'calm', 'tired / flat',
    'engaged', 'tense') rather than defaulting to a static 'neutral'."""
    strong = {k: v for k, v in prob_map.items() if k != "neutral"}
    top_e = max(strong, key=strong.get) if strong else None
    if top_e and strong[top_e] >= 0.35:
        return _FRIENDLY.get(top_e, top_e)

    hi_a, lo_a = arousal >= 0.50, arousal < 0.33
    hi_v, lo_v = valence >= 0.55, valence < 0.45
    if lo_a and lo_v:
        return "tired / flat"
    if lo_a and hi_v:
        return "calm"
    if hi_a and hi_v:
        return "engaged"
    if hi_a and lo_v:
        return "tense"
    if lo_a:
        return "low energy"
    if hi_a:
        return "alert"
    return "neutral"


def predict(frame_or_face, box=None):
    """Estimate facial emotion + affect.

    frame_or_face : an RGB (or grayscale) image — either a full frame (pass `box`)
                    or an already-cropped face.
    box           : ((y1,y2),(x1,x2)) face box from open-rppg; if given, the face
                    is cropped from frame_or_face first.

    Returns a dict:
        {"arousal": 0..1, "valence": 0..1, "emotional_state": <top label>,
         "source": "fer-ferplus", "probs": {label: prob}}
    or None if the model is unavailable or the input can't be processed.
    Never raises.
    """
    try:
        sess = load_model()
        if sess is None:
            return None

        face = frame_or_face
        if box is not None:
            face = _crop(np.asarray(frame_or_face), box)
            if face is None or face.size == 0:
                return None

        x = _preprocess(face)
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
        probs = _softmax(np.asarray(out).reshape(-1)[:len(LABELS)])
        prob_map = {lbl: float(p) for lbl, p in zip(LABELS, probs)}

        # probability-weighted circumplex position
        arousal = sum(prob_map[l] * AV_MAP[l][0] for l in LABELS)
        valence = sum(prob_map[l] * AV_MAP[l][1] for l in LABELS)

        return {
            "arousal": round(float(arousal), 3),
            "valence": round(float(valence), 3),
            "emotional_state": _state_label(arousal, valence, prob_map),
            "source": SOURCE,
            "probs": {k: round(v, 4) for k, v in prob_map.items()},
        }
    except Exception as e:
        print(f"[face_emotion] WARNING: predict failed: {e!r}", flush=True)
        return None


if __name__ == "__main__":
    if len(sys.argv) > 1:
        import cv2
        path = sys.argv[1]
        img = cv2.imread(path)  # BGR
        if img is None:
            print(f"could not read image: {path}")
            sys.exit(1)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        print(predict(rgb))
    else:
        # smoke test: dummy 64x64 gray face
        dummy = (np.random.rand(64, 64) * 255).astype(np.uint8)
        print(predict(dummy))
