"""
Pretrained speech-emotion recognition (dimensional) for the voice leg.

Uses audeering's wav2vec2 model fine-tuned on MSP-Podcast, which regresses the
three affect dimensions (each in [0,1]):
    arousal  — calm/sleepy (0) .. excited/alert (1)   <- low arousal ≈ fatigue
    dominance— submissive (0) .. dominant (1)
    valence  — negative (0) .. positive (1)

Fully offline after the first download (~1.2 GB, cached in ~/.cache/huggingface).
Runs in 'papagei_env' (torch + transformers + soundfile).

    from voice_emotion import predict
    predict("voice.wav")  -> {"arousal":.., "dominance":.., "valence":..}
"""
import numpy as np
import torch
import torch.nn as nn
from transformers import Wav2Vec2Model, Wav2Vec2PreTrainedModel, Wav2Vec2Processor

MODEL_NAME = "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"

_processor = None
_model = None


class _RegressionHead(nn.Module):
    """audeering's affect-regression head (matches the released checkpoint)."""

    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.final_dropout)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, x):
        x = self.dropout(x)
        x = torch.tanh(self.dense(x))
        x = self.dropout(x)
        return self.out_proj(x)


class EmotionModel(Wav2Vec2PreTrainedModel):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.wav2vec2 = Wav2Vec2Model(config)
        self.classifier = _RegressionHead(config)
        self.init_weights()

    def forward(self, input_values):
        hidden = self.wav2vec2(input_values)[0]
        hidden = torch.mean(hidden, dim=1)
        return hidden, self.classifier(hidden)


def _load():
    global _processor, _model
    if _model is None:
        _processor = Wav2Vec2Processor.from_pretrained(MODEL_NAME)
        _model = EmotionModel.from_pretrained(MODEL_NAME).eval()
    return _processor, _model


def predict(sig, sr=16000):
    """sig: path to a wav, or a float mono array. Returns dim scores in [0,1]."""
    if isinstance(sig, str):
        import soundfile as sf
        sig, sr = sf.read(sig)
    sig = np.asarray(sig, dtype=np.float32)
    if sig.ndim > 1:
        sig = sig.mean(axis=1)

    proc, model = _load()
    inputs = proc(sig, sampling_rate=16000, return_tensors="pt")
    with torch.no_grad():
        _, logits = model(inputs.input_values)
    a, d, v = logits[0].tolist()
    return {"arousal": float(a), "dominance": float(d), "valence": float(v)}


if __name__ == "__main__":
    import sys
    print(predict(sys.argv[1] if len(sys.argv) > 1 else "voice.wav"))
