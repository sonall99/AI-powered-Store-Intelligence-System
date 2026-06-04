# detection_pipeline/calibrate_staff_hsv.py
"""
Calibrate staff uniform HSV range for a new store.
Run on a frame where staff are clearly visible.

Usage:
    python detection_pipeline/calibrate_staff_hsv.py \
        --clip clips/CAM_FLOOR_S2.mp4 \
        --frame 300
"""
import cv2
import numpy as np
import argparse

def calibrate(clip_path, frame_num=300):
    cap = cv2.VideoCapture(clip_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("Could not read frame")
        return

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Click on staff uniform to sample HSV
    samples = []

    def mouse_callback(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            h, s, v = hsv[y, x]
            samples.append((int(h), int(s), int(v)))
            print(f"  Sampled HSV at ({x},{y}): H={h} S={s} V={v}")

    cv2.namedWindow("Sample Staff Uniform - Click on pink clothing")
    cv2.setMouseCallback("Sample Staff Uniform - Click on pink clothing", mouse_callback)

    print("Click on staff uniform areas. Press 'q' when done.")
    print("Aim for torso region of staff members.")

    while True:
        display = frame.copy()
        cv2.putText(display, f"Samples: {len(samples)} | Click staff uniform | q=quit",
                   (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 2)
        cv2.imshow("Sample Staff Uniform - Click on pink clothing", display)
        if cv2.waitKey(50) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()

    if samples:
        h_vals = [s[0] for s in samples]
        s_vals = [s[1] for s in samples]
        v_vals = [s[2] for s in samples]

        margin = 20
        lower = [max(0, min(h_vals)-margin),
                 max(0, min(s_vals)-30),
                 max(0, min(v_vals)-30)]
        upper = [min(180, max(h_vals)+margin),
                 min(255, max(s_vals)+30),
                 min(255, max(v_vals)+30)]

        print(f"\n{'='*40}")
        print(f"Recommended HSV range for store_layout.json:")
        print(f'"hsv_lower": {lower},')
        print(f'"hsv_upper": {upper}')
        print(f"Sampled from {len(samples)} points")
        print(f"H range: {min(h_vals)}-{max(h_vals)}")
        print(f"S range: {min(s_vals)}-{max(s_vals)}")
        print(f"V range: {min(v_vals)}-{max(v_vals)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", required=True)
    parser.add_argument("--frame", type=int, default=300)
    args = parser.parse_args()
    calibrate(args.clip, args.frame)