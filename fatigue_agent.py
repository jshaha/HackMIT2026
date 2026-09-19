"""
Agentic fatigue loop — the decision node of the pipeline.

Fuses the two capture legs into a single "too fatigued?" call:
  - heart leg  (reading.json):        HR, BR, HRV, signal quality
  - voice leg  (voice_features.json): arousal_index, fatigue_index, pauses, rate

Design (matches the whiteboard "agentic loop to classify fatigue"):
  1. Deterministic RULE CORE fuses grounded numbers into a fatigue_score [0,1]
     and a next_action (continue | recheck | halt). This ALWAYS runs — no
     network, no API key, fully reproducible.
  2. Optional CLAUDE REASONING layer: if ANTHROPIC_API_KEY is set, Claude sees
     the same numbers + the rule score and returns a decision with an
     explanation. It's grounded in the real features, not asked to invent them.
     Without a key we fall back to a rule-based explanation.

The "loop": low signal quality or a missing leg -> next_action="recheck"
(inconclusive, re-capture) rather than a false-confident verdict. Not fatigued
-> "continue" (keep monitoring). Fatigued -> "halt" (flag the session).

Run in any env with numpy (papagei_env or rppg both work):
    python fatigue_agent.py \
        --reading vitals-dashboard/public/reading.json \
        --voice voice_features.json \
        --out decision.json
"""
import argparse
import json
import os
import urllib.request

THRESHOLD = 0.60          # fatigue_score >= this -> too fatigued
MIN_SQI = 0.50            # below this the heart leg is untrustworthy


def _load_dotenv():
    """Populate os.environ from a sibling .env (gitignored) without overriding
    anything already set in the real environment."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _clip01(x):
    return float(min(1.0, max(0.0, x)))


def load(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def fuse(reading, voice):
    """Weighted fusion of available fatigue markers -> (score, factors, quality)."""
    factors = []          # human-readable contributions
    parts = []            # (weight, value_in_0_1)

    sqi = None
    if reading:
        sqi = reading.get("sqi")
        hr = reading.get("hr")
        br = reading.get("br")
        rmssd = (reading.get("hrv") or {}).get("rmssd")

        # Low HRV (RMSSD) tracks fatigue/strain when available.
        if rmssd is not None:
            v = _clip01(1 - rmssd / 50.0)   # 50 ms ~ healthy; lower -> fatigue
            parts.append((0.20, v))
            factors.append(f"HRV RMSSD {rmssd} ms -> {'low' if v>0.5 else 'ok'}")

        # Resting-low HR + drowsiness, or very high HR (strain) both notable.
        if hr is not None:
            if hr < 55:
                parts.append((0.10, _clip01((55 - hr) / 20.0)))
                factors.append(f"HR {hr} BPM (low)")
            elif hr > 100:
                parts.append((0.10, _clip01((hr - 100) / 40.0)))
                factors.append(f"HR {hr} BPM (elevated)")
            else:
                factors.append(f"HR {hr} BPM (normal)")

        # Abnormal breathing rate (very slow = drowsy, very fast = distress).
        if br is not None:
            dev = max(0.0, (10 - br) / 6.0, (br - 20) / 12.0)
            if dev > 0:
                parts.append((0.10, _clip01(dev)))
                factors.append(f"BR {br}/min (abnormal)")
            else:
                factors.append(f"BR {br}/min (normal)")

    if voice:
        fi = voice.get("fatigue_index")
        ar = voice.get("arousal_index")
        pause = voice.get("pause_ratio")
        rate = voice.get("speech_rate_hz")
        if fi is not None:
            parts.append((0.40, _clip01(fi)))
            factors.append(f"voice fatigue_index {fi}")
        if ar is not None:
            parts.append((0.20, _clip01(1 - ar)))
            factors.append(f"voice arousal_index {ar} ({'low' if ar<0.4 else 'ok'})")
        if pause is not None and pause > 0.35:
            factors.append(f"long pauses (pause_ratio {pause})")
        if rate is not None and rate < 2.5:
            factors.append(f"slow speech ({rate} syl/s)")

    if not parts:
        return None, ["no usable signals"], sqi

    total_w = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / total_w
    return _clip01(score), factors, sqi


def decide(score, factors, sqi, have_reading, have_voice):
    """Turn a fused score into a verdict + next_action (the loop control)."""
    legs = int(have_reading) + int(have_voice)
    low_quality = (sqi is not None and sqi < MIN_SQI)

    if score is None or legs == 0 or low_quality:
        reason = ("Signal insufficient for a confident call"
                  + (f" (SQI {sqi} < {MIN_SQI})" if low_quality else "")
                  + (" — only one leg captured." if legs == 1 else "."))
        return {
            "too_fatigued": None,
            "fatigue_score": score,
            "confidence": 0.2,
            "next_action": "recheck",
            "reasoning": reason,
            "key_factors": factors,
        }

    too_fatigued = score >= THRESHOLD
    # confidence: distance from threshold, scaled by data coverage & quality.
    conf = _clip01(abs(score - THRESHOLD) / 0.4)
    conf *= 0.7 + 0.15 * legs                      # more legs -> more confident
    if sqi is not None:
        conf *= _clip01(0.5 + sqi / 2)
    conf = round(_clip01(conf), 2)

    return {
        "too_fatigued": bool(too_fatigued),
        "fatigue_score": round(score, 3),
        "confidence": conf,
        "next_action": "halt" if too_fatigued else "continue",
        "reasoning": ("Fatigue markers exceed threshold — recommend pausing the session."
                      if too_fatigued else
                      "Fatigue markers within acceptable range — patient can continue."),
        "key_factors": factors,
    }


PROMPT = (
    "You are the fatigue-classification node in a clinical-trial monitoring "
    "pipeline. Below are grounded biometric + vocal features for a patient and "
    "a deterministic rule-based fatigue score in [0,1] (>=0.6 = too fatigued). "
    "Decide whether the patient is too fatigued to safely continue. Weigh the "
    "rule score but you may override it if the raw features clearly disagree. "
    "Respond ONLY with compact JSON: "
    '{"too_fatigued": true/false/null, "confidence": 0-1, '
    '"next_action": "continue"|"recheck"|"halt", "reasoning": "one sentence"}'
)


def _pick_provider():
    """Return (name, url, headers, include_model_field) for whichever LLM is
    configured, or None. Preference: Azure OpenAI -> OpenAI -> Anthropic."""
    az_key = os.environ.get("AZURE_OPENAI_API_KEY")
    az_ep = os.environ.get("AZURE_OPENAI_ENDPOINT")
    az_dep = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if az_key and az_ep and az_dep:
        ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
        url = f"{az_ep.rstrip('/')}/openai/deployments/{az_dep}/chat/completions?api-version={ver}"
        return ("azure:" + az_dep, url,
                {"content-type": "application/json", "api-key": az_key}, False)

    oa_key = os.environ.get("OPENAI_API_KEY")
    if oa_key:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        return ("openai:" + model, base + "/chat/completions",
                {"content-type": "application/json",
                 "authorization": f"Bearer {oa_key}"}, True)

    an_key = os.environ.get("ANTHROPIC_API_KEY")
    if an_key:
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        return ("anthropic:" + model, "https://api.anthropic.com/v1/messages",
                {"content-type": "application/json", "x-api-key": an_key,
                 "anthropic-version": "2023-06-01"}, "anthropic")
    return None


def llm_refine(verdict, reading, voice):
    """Optional: let an LLM reason over the same numbers and rewrite the verdict.
    Grounded — it is handed the features and the rule score, not asked to guess.
    Provider auto-selected from env (Azure OpenAI / OpenAI / Anthropic)."""
    prov = _pick_provider()
    if not prov:
        return verdict, ("rule-core (no LLM key: set OPENAI_API_KEY, "
                         "AZURE_OPENAI_* or ANTHROPIC_API_KEY)")
    name, url, headers, model_mode = prov

    payload = {
        "rule_score": verdict.get("fatigue_score"),
        "rule_verdict": verdict.get("too_fatigued"),
        "heart_leg": reading,
        "voice_leg": voice,
    }
    user_msg = PROMPT + "\n\n" + json.dumps(payload)

    if model_mode == "anthropic":
        body = {"model": name.split(":", 1)[1], "max_tokens": 300,
                "messages": [{"role": "user", "content": user_msg}]}
    else:  # OpenAI-style chat completions (OpenAI or Azure)
        body = {"max_tokens": 300, "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": user_msg}]}
        if model_mode is True:  # OpenAI needs the model field; Azure gets it from the URL
            body["model"] = name.split(":", 1)[1]

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        if model_mode == "anthropic":
            text = "".join(b.get("text", "") for b in resp.get("content", []))
        else:
            text = resp["choices"][0]["message"]["content"]
        text = text[text.find("{"): text.rfind("}") + 1]
        llm = json.loads(text)
        verdict = {**verdict, **{k: llm[k] for k in
                   ("too_fatigued", "confidence", "next_action", "reasoning") if k in llm}}
        return verdict, name
    except Exception as e:
        return verdict, f"rule-core ({name} error: {e})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reading", default="vitals-dashboard/public/reading.json")
    ap.add_argument("--voice", default="voice_features.json")
    ap.add_argument("--out", default="decision.json")
    ap.add_argument("--no-llm", action="store_true", help="skip the Claude layer")
    args = ap.parse_args()

    reading = load(args.reading)
    voice = load(args.voice)

    score, factors, sqi = fuse(reading, voice)
    verdict = decide(score, factors, sqi, reading is not None, voice is not None)

    engine = "rule-core"
    if not args.no_llm:
        verdict, engine = llm_refine(verdict, reading, voice)
    verdict["engine"] = engine
    verdict["inputs"] = {"heart_leg": reading is not None, "voice_leg": voice is not None}

    with open(args.out, "w") as f:
        json.dump(verdict, f, indent=2)

    # Merge voice + verdict back into reading.json so the dashboard shows the
    # whole picture from its single "Load reading" fetch.
    if reading is not None and os.path.exists(args.reading):
        reading["voice"] = voice
        reading["fatigue"] = verdict
        with open(args.reading, "w") as f:
            json.dump(reading, f)

    v = verdict
    tf = v.get("too_fatigued")
    label = {True: "⚠ TOO FATIGUED", False: "✓ OK TO CONTINUE",
             None: "? INCONCLUSIVE"}[tf]
    print("")
    print("╔═ Fatigue decision " + "═" * 29)
    print(f"║ Verdict     : {label}")
    print(f"║ Fatigue     : {v.get('fatigue_score')}   (threshold {THRESHOLD})")
    print(f"║ Confidence  : {v.get('confidence')}")
    print(f"║ Next action : {v.get('next_action').upper()}")
    print(f"║ Engine      : {engine}")
    print(f"║ Legs used   : heart={v['inputs']['heart_leg']} voice={v['inputs']['voice_leg']}")
    print("║ Why         :")
    print(f"║   {v.get('reasoning')}")
    for fct in v.get("key_factors", []):
        print(f"║   • {fct}")
    print("╚" + "═" * 48)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
