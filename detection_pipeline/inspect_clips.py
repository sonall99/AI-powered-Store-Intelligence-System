# detection_pipeline/inspect_clips.py
"""
Run this FIRST when clips arrive.
Tells you: duration, fps, resolution, start timestamp for each camera.
Saves a calibration frame for zone polygon mapping.

Usage:
    python detection_pipeline/inspect_clips.py
"""
import cv2
import os
import re
import json
from datetime import datetime

CLIPS_DIR = "clips"
CALIBRATION_DIR = "calibration"
os.makedirs(CALIBRATION_DIR, exist_ok=True)

def extract_burned_timestamp(frame):
    """
    CP IP Cam burns timestamp in top-right corner.
    Format: DD/MM/YYYY HH:MM:SS
    We crop that region and use basic text matching.
    Falls back to None if unreadable.
    """
    try:
        import pytesseract
        h, w = frame.shape[:2]
        # Top-right corner — timestamp region
        region = frame[0:80, w-420:w]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
        text = pytesseract.image_to_string(thresh, config='--psm 7 -c tessedit_char_whitelist=0123456789/:')
        match = re.search(r'(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2}:\d{2})', text)
        if match:
            return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%d/%m/%Y %H:%M:%S")
    except ImportError:
        pass  # pytesseract not installed — use manual entry
    return None


def inspect_clip(filename):
    path = os.path.join(CLIPS_DIR, filename)
    cap = cv2.VideoCapture(path)
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_sec = total_frames / fps if fps > 0 else 0
    
    # Save frame at 5 seconds for calibration
    target_frame = int(fps * 5)
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    
    start_time = None
    if ret:
        # Save calibration frame
        cal_path = os.path.join(CALIBRATION_DIR, f"{filename.replace('.mp4','')}_calibration.jpg")
        cv2.imwrite(cal_path, frame)
        
        # Try to extract burned-in timestamp
        start_time = extract_burned_timestamp(frame)
    
    cap.release()
    
    result = {
        "file": filename,
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "total_frames": total_frames,
        "duration_sec": round(duration_sec, 1),
        "duration_min": round(duration_sec / 60, 2),
        "start_time_detected": start_time.isoformat() if start_time else "MANUAL_ENTRY_NEEDED",
        "calibration_frame": f"calibration/{filename.replace('.mp4','')}_calibration.jpg"
    }
    
    return result


def main():
    clips = sorted([f for f in os.listdir(CLIPS_DIR) if f.endswith('.mp4')])
    
    if not clips:
        print(f"No MP4 files found in {CLIPS_DIR}/")
        print("Put your CAM1.mp4 ... CAM5.mp4 files there first.")
        return
    
    print(f"\nFound {len(clips)} clips:\n")
    all_results = []
    
    for clip in clips:
        result = inspect_clip(clip)
        all_results.append(result)
        
        print(f"{'='*50}")
        print(f"Camera: {result['file']}")
        print(f"Resolution: {result['width']}x{result['height']}")
        print(f"FPS: {result['fps']}")
        print(f"Duration: {result['duration_sec']}s ({result['duration_min']} min)")
        print(f"Total frames: {result['total_frames']}")
        print(f"Start timestamp: {result['start_time_detected']}")
        print(f"Calibration frame: {result['calibration_frame']}")
    
    # Save results for use by detect.py
    with open("calibration/clip_info.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n\nSaved to calibration/clip_info.json")
    print("Open the calibration/*.jpg files to identify which camera covers which zone.")


if __name__ == "__main__":
    main()