"""
Stage 1 of the vitals pipeline: capture a BVP (pulse) waveform.

Run in the 'rppg' conda env:
    conda activate rppg
    python capture_bvp.py --seconds 30            # webcam for 30s
    python capture_bvp.py --video path/to/clip.mp4 # or a recorded face video

Saves bvp.npz  (arrays: bvp, fs)  for Stage 2 (bvp_to_papagei.py).
Sit still, face the camera, good lighting. Press 'q' to stop early.
"""
import argparse
import time
import numpy as np
import cv2
import rppg


def capture_webcam(model, seconds, camera=0):
    print(f"Opening camera index {camera}. Capturing ~{seconds}s. Sit still, face the camera.")
    t0 = None
    with model.video_capture(camera):
        for frame, box in model.preview:
            if t0 is None:
                t0 = time.time()
            elapsed = time.time() - t0
            disp = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            if box is not None:
                (y1, y2), (x1, x2) = box[0], box[1]
                cv2.rectangle(disp, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(disp, f"Capturing {elapsed:0.1f}/{seconds}s",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.imshow("Capturing BVP (press q to stop)", disp)
            if elapsed >= seconds or (cv2.waitKey(1) & 0xFF == ord("q")):
                break
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=None, help="video file (omit to use webcam)")
    ap.add_argument("--seconds", type=int, default=30, help="webcam capture duration")
    ap.add_argument("--camera", type=int, default=0, help="camera index (0=built-in, 1=iPhone/Continuity)")
    ap.add_argument("--out", default="bvp.npz")
    args = ap.parse_args()

    model = rppg.Model()

    if args.video:
        print(f"Processing video: {args.video}")
        result = model.process_video(args.video)
        print("process_video result:", result)
    else:
        capture_webcam(model, args.seconds, camera=args.camera)

    bvp, ts = model.bvp(start=0, end=None)
    bvp = np.asarray(bvp, dtype=np.float32)
    if bvp.size < model.fps * 4:
        raise SystemExit("Not enough signal captured. Try longer / better lighting.")

    # sanity: report HR from open-rppg itself
    hr = model.hr(start=0, end=None)
    print(f"open-rppg HR: {hr.get('hr') if hr else None} BPM | "
          f"SQI: {hr.get('SQI') if hr else None} | fps: {model.fps:.2f}")

    np.savez(args.out, bvp=bvp, fs=np.float32(model.fps))
    print(f"Saved {args.out}: {bvp.shape[0]} samples @ {model.fps:.2f} Hz "
          f"(~{bvp.shape[0]/model.fps:.1f}s)")


if __name__ == "__main__":
    main()
