"""
Lightweight topic-coverage matcher for the visit-agenda tracker.

Replaces the old per-round LLM `coverage_check` with fast VECTOR SEARCH:
embed the recent transcript (split into overlapping sentence windows) and each
agenda topic, then use COSINE SIMILARITY to flag topics that were likely
discussed. No network / no LLM latency once the model is cached.

Two backends, chosen automatically:
  * "minilm-cosine": sentence-transformers "all-MiniLM-L6-v2" (torch-based, safe
    in this env; ~80MB, cached under the HF cache). Preferred.
  * "tfidf-cosine":  pure-numpy TF-IDF cosine over a vocabulary built from the
    topics + transcript. Graceful fallback if sentence-transformers can't load.

We never import onnxruntime / webrtcvad (they segfault in this env).

Similarity threshold
--------------------
MiniLM sentence embeddings put a real paraphrase of a topic at cosine ~0.4-0.6
against the topic text, while unrelated small-talk sits well below (~0.1-0.25).
DEFAULT_THRESHOLD is 0.45 for MiniLM -- comfortably above noise, low enough to
catch genuine paraphrases (see the scripted self-test at the bottom of this
file, run with `python agenda_match.py`).

TF-IDF cosine is sparser/noisier, so its fallback threshold is lower (0.22):
lexical overlap of a real discussion still clears it, unrelated text does not.
"""
import math
import re

import numpy as np

DEFAULT_THRESHOLD = 0.45          # minilm-cosine
TFIDF_THRESHOLD = 0.22            # tfidf-cosine fallback

_MODEL = None                     # lazily-loaded SentenceTransformer (or False)


# --------------------------------------------------------------------------- #
# transcript windowing
# --------------------------------------------------------------------------- #
def split_windows(transcript, max_windows=40):
    """Split a transcript into short candidate snippets to match topics against.

    We use sentence-ish units plus 2-sentence sliding pairs so a topic that is
    only satisfied across a Q/A turn boundary ("Any side effects?" / "A bit
    dizzy") still has a window that covers it.
    """
    text = (transcript or "").strip()
    if not text:
        return []
    parts = [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text) if s.strip()]
    if not parts:
        parts = [text]
    windows = list(parts)
    for i in range(len(parts) - 1):
        windows.append(parts[i] + " " + parts[i + 1])
    # keep only the most recent snippets (rolling transcript can grow long)
    if len(windows) > max_windows:
        windows = windows[-max_windows:]
    # de-dup while preserving order
    seen, out = set(), []
    for w in windows:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


# --------------------------------------------------------------------------- #
# MiniLM backend
# --------------------------------------------------------------------------- #
def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL or None
    try:
        from sentence_transformers import SentenceTransformer
        # CPU is plenty for 384-dim / a handful of short strings and avoids
        # any MPS surprises; the model is cached after first download.
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    except Exception as e:  # pragma: no cover - depends on install/network
        print(f"[agenda_match] sentence-transformers unavailable ({e}); "
              f"falling back to TF-IDF cosine.")
        _MODEL = False
    return _MODEL or None


def _minilm_scores(topics, windows):
    """Return best (score, window) per topic using MiniLM cosine."""
    model = _load_model()
    if model is None:
        return None
    topic_emb = model.encode(topics, normalize_embeddings=True,
                             show_progress_bar=False)
    win_emb = model.encode(windows, normalize_embeddings=True,
                           show_progress_bar=False)
    topic_emb = np.asarray(topic_emb, dtype=np.float32)
    win_emb = np.asarray(win_emb, dtype=np.float32)
    sims = topic_emb @ win_emb.T          # already unit-norm -> cosine
    out = []
    for i in range(len(topics)):
        j = int(np.argmax(sims[i]))
        out.append((float(sims[i, j]), windows[j]))
    return out


# --------------------------------------------------------------------------- #
# TF-IDF fallback backend (pure numpy)
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STOP = frozenset("""a an and are as at be been being but by can could did do does
doing for from had has have having he her him his how i if in into is it its me my
of on or our so than that the their them then there these they this to up us was we
were what when where which who will with you your""".split())


def _tokens(s):
    return [t for t in _TOKEN_RE.findall((s or "").lower())
            if t not in _STOP and len(t) > 1]


def _tfidf_scores(topics, windows):
    """Best (score, window) per topic using TF-IDF cosine over a shared vocab."""
    docs = [_tokens(t) for t in topics] + [_tokens(w) for w in windows]
    vocab = {}
    for d in docs:
        for tok in d:
            if tok not in vocab:
                vocab[tok] = len(vocab)
    if not vocab:
        return [(0.0, windows[0] if windows else "") for _ in topics]
    n_docs = len(docs)
    df = np.zeros(len(vocab))
    for d in docs:
        for tok in set(d):
            df[vocab[tok]] += 1
    idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0

    def vec(tokens):
        v = np.zeros(len(vocab))
        for tok in tokens:
            v[vocab[tok]] += 1.0
        if v.sum() > 0:
            v = v / v.sum()            # term frequency
        v = v * idf
        norm = math.sqrt(float(v @ v))
        return v / norm if norm > 0 else v

    topic_vecs = [vec(_tokens(t)) for t in topics]
    win_vecs = [vec(_tokens(w)) for w in windows]
    out = []
    for tv in topic_vecs:
        best_s, best_w = 0.0, (windows[0] if windows else "")
        for w, wv in zip(windows, win_vecs):
            s = float(tv @ wv)
            if s > best_s:
                best_s, best_w = s, w
        out.append((best_s, best_w))
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def match_topics(topics, transcript, threshold=None):
    """Score each topic against the transcript.

    Returns (results, method) where method is "minilm-cosine" | "tfidf-cosine"
    and results is a list aligned with `topics`:
        {"similarity": float, "evidence": str|None, "suggested": bool}
    `similarity` is rounded; `suggested` is True iff similarity >= threshold.
    """
    windows = split_windows(transcript)
    if not topics or not windows:
        return ([{"similarity": 0.0, "evidence": None, "suggested": False}
                 for _ in topics],
                "minilm-cosine" if _load_model() is not None else "tfidf-cosine")

    scored = _minilm_scores(topics, windows)
    if scored is not None:
        method = "minilm-cosine"
        thr = DEFAULT_THRESHOLD if threshold is None else threshold
    else:
        scored = _tfidf_scores(topics, windows)
        method = "tfidf-cosine"
        thr = TFIDF_THRESHOLD if threshold is None else threshold

    results = []
    for score, win in scored:
        suggested = score >= thr
        results.append({
            "similarity": round(float(score), 4),
            "evidence": win if suggested else None,
            "suggested": suggested,
        })
    return results, method


# --------------------------------------------------------------------------- #
# self-test (no mic, no db):  python agenda_match.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    topics = [
        "Review the patient's sleep log from the past week",
        "Ask about side effects of the beta-blocker",
        "Discuss medication adherence",
        "Check on exercise and diet since last visit",
    ]
    transcript = (
        "Hi, thanks for coming in today. How have you been feeling? "
        "Did you write down how you slept every night this past week? "
        "Yes, I kept the log, I averaged about six hours a night. "
        "Have you been remembering to take your beta blocker every day? "
        "Mostly, I missed a couple of doses on the weekend. "
        "Okay. And have you noticed any dizziness or a slow heartbeat from the medication?"
    )
    results, method = match_topics(topics, transcript)
    print(f"method = {method}")
    for t, r in zip(topics, results):
        tag = "SUGGESTED" if r["suggested"] else "pending  "
        print(f"  [{tag}] sim={r['similarity']:.3f}  {t}")
        if r["evidence"]:
            print(f"             evidence: {r['evidence']!r}")
