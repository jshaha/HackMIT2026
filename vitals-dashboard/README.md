# VITALS // rPPG — contactless vitals dashboard

A light clinical "instrument" dashboard for the no-wearable vitals demo:
webcam/iPhone pulse (open-rppg) → heart rate + HRV → PaPaGei feature embedding.

**Stack:** Vite + React + TypeScript + Tailwind v4 · **Motion** (motion.dev) for
transitions · **Bklit UI** (visx) for charts.

**Design:** redesigned against [impeccable](https://impeccable.style)'s anti-slop
rules — no cyan-on-dark, no glow shadows or radial halos, one meaningful accent
(arterial red = the pulse), flat card hierarchy, neutral elevation, IBM Plex.
The Kokonut beams background was removed (a decorative colored haze conflicts
with the anti-glow direction); `src/components/kokonutui/` is left unused.

## Run

```bash
cd vitals-dashboard
npm install
npm run dev        # http://localhost:5173
```

Ships with a realistic demo reading, so it looks alive immediately.

## Load a real capture

1. Capture + (optionally) embed with the Python pipeline in the parent folder:
   ```bash
   ./capture_iphone.sh                          # -> iphone_bvp.npz
   conda activate papagei_env
   python bvp_to_papagei.py --in iphone_bvp.npz --out iphone_embeddings.npy
   ```
2. Export to JSON the dashboard can read:
   ```bash
   conda activate rppg
   python ../export_reading.py \
     --in ../iphone_bvp.npz \
     --embedding ../iphone_embeddings.npy \
     --out public/reading.json
   ```
3. In the UI, click **LOAD READING** (fetches `/reading.json`), or the upload
   icon to pick any exported `reading.json` from disk.

## Where things live

| Piece | File |
|---|---|
| Theme / palette / fonts | `src/index.css` |
| Demo data + waveform gen | `src/lib/demoData.ts` |
| Reading JSON shape | `src/lib/types.ts` |
| Oscilloscope pulse trace | `src/components/PulseWave.tsx` |
| SQI ring gauge (Bklit) | `src/components/SqiRing.tsx` |
| HR trend (Bklit area) | `src/components/HrTrendChart.tsx` |
| HRV radar (Bklit) | `src/components/HrvRadar.tsx` |
| PaPaGei fingerprint | `src/components/EmbeddingFingerprint.tsx` |
| Beams background (Kokonut) | `src/components/kokonutui/beams-background.tsx` |
| Bklit chart primitives | `src/components/charts/` |

Contactless estimates — not medical grade. SQI < 0.5 = low-confidence reading.
