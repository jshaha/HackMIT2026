"""
Live heart-rate from your MacBook webcam using open-rppg.

Run inside the 'rppg' conda env:
    conda activate rppg
    python webcam_hr_demo.py

Press 'q' in the video window to quit. Sit still, face the camera, decent lighting.
The first run downloads the model. Grant camera permission when macOS asks.

Outputs (from model.hr()): hr (BPM), SQI (0-1 signal quality), hrv metrics
(sdnn/rmssd/pnn50/LF/HF) and breathingrate (respiration).
"""
import time
import cv2
import rppg

model = rppg.Model()
print("Model loaded. Opening camera (index 0)...")

with model.video_capture(0):
    last = 0.0
    hr = None
    for frame, box in model.preview:
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        now = time.time()
        if now - last > 1.0:                 # recompute once per second
            result = model.hr(start=-10)     # use last 10s of signal
            if result and result.get("hr"):
                hr = result["hr"]
                sqi = result.get("SQI")
                br = (result.get("hrv") or {}).get("breathingrate")
                print(f"HR: {hr:.1f} BPM | SQI: {sqi} | resp: {br}")
            last = now

        if box is not None:
            (y1, y2), (x1, x2) = box[0], box[1]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            if hr is not None:
                cv2.putText(frame, f"HR: {hr:.1f}", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

        cv2.imshow("rPPG Monitor  (press q to quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

cv2.destroyAllWindows()
