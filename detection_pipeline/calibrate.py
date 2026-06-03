# detection_pipeline/calibrate.py
"""
Interactive tripwire calibration tool.
Run this on any new clip to set correct tripwire coordinates.

Usage:
    python detection_pipeline/calibrate.py --clip clips/CAM_3.mp4 --camera CAM_3

Instructions:
    - A frame from the clip will open
    - Click two points to define the tripwire line
    - Press 'n' to try next frame, 'q' to quit and save
    - Coordinates are saved to config/store_layout.json automatically
"""

import cv2
import json
import argparse
import os
import sys

clicks = []

def mouse_callback(event, x, y, flags, param):
    global clicks
    if event == cv2.EVENT_LBUTTONDOWN:
        clicks.append((x, y))
        print(f"  Clicked: ({x}, {y})")

def calibrate(clip_path: str, camera_id: str, layout_path: str):
    global clicks
    
    cap = cv2.VideoCapture(clip_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    frame_positions = [int(total * p) for p in [0.1, 0.2, 0.3, 0.5]]
    frame_idx = 0
    
    print(f"\nCalibrating tripwire for {camera_id}")
    print("Instructions:")
    print("  LEFT CLICK = place tripwire point")
    print("  n = next frame")
    print("  r = reset clicks")
    print("  s = save and exit")
    print("  q = quit without saving")
    print("\nClick TWO points to define the tripwire line.\n")
    
    cv2.namedWindow("Calibrate Tripwire")
    cv2.setMouseCallback("Calibrate Tripwire", mouse_callback)
    
    current_frame_pos = 0
    
    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_positions[current_frame_pos % len(frame_positions)])
        ret, frame = cap.read()
        if not ret:
            break
        
        h, w = frame.shape[:2]
        display = frame.copy()
        
        # Draw grid for reference
        cv2.line(display, (w//3, 0), (w//3, h), (40,40,40), 1)
        cv2.line(display, (2*w//3, 0), (2*w//3, h), (40,40,40), 1)
        cv2.line(display, (0, h//2), (w, h//2), (40,40,40), 1)
        
        # Draw existing clicks
        for i, (cx, cy) in enumerate(clicks):
            cv2.circle(display, (cx, cy), 8, (0, 255, 0), -1)
            cv2.putText(display, f"P{i+1}({cx},{cy})", (cx+10, cy),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)
        
        # Draw tripwire if 2 points selected
        if len(clicks) >= 2:
            cv2.line(display, clicks[0], clicks[1], (0, 0, 255), 3)
            cv2.putText(display, "TRIPWIRE", 
                       ((clicks[0][0]+clicks[1][0])//2, 
                        (clicks[0][1]+clicks[1][1])//2 - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2)
        
        # Instructions overlay
        cv2.putText(display, f"Camera: {camera_id} | Frame: {frame_positions[current_frame_pos % len(frame_positions)]}",
                   (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
        cv2.putText(display, f"Clicks: {len(clicks)}/2 | n=next r=reset s=save q=quit",
                   (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200,200,200), 1)
        
        cv2.imshow("Calibrate Tripwire", display)
        key = cv2.waitKey(50) & 0xFF
        
        if key == ord('n'):
            current_frame_pos += 1
            print(f"  Next frame: {frame_positions[current_frame_pos % len(frame_positions)]}")
        elif key == ord('r'):
            clicks = []
            print("  Clicks reset")
        elif key == ord('s') and len(clicks) >= 2:
            # Save to layout
            with open(layout_path) as f:
                layout = json.load(f)
            
            for cam in layout["cameras"]:
                if cam["camera_id"] == camera_id:
                    cam["tripwire"] = {
                        "x1": clicks[0][0],
                        "y1": clicks[0][1],
                        "x2": clicks[1][0],
                        "y2": clicks[1][1],
                        "inside_is": "top",
                        "buffer_px": 40,
                        "notes": f"Calibrated manually on {clip_path}"
                    }
                    cam["has_tripwire"] = True
                    break
            
            with open(layout_path, "w") as f:
                json.dump(layout, f, indent=2)
            
            print(f"\n  Saved tripwire to {layout_path}:")
            print(f"  x1={clicks[0][0]}, y1={clicks[0][1]}")
            print(f"  x2={clicks[1][0]}, y2={clicks[1][1]}")
            break
        elif key == ord('q'):
            print("  Quit without saving")
            break
    
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", required=True)
    parser.add_argument("--camera", required=True, help="camera_id from store_layout.json")
    parser.add_argument("--layout", default="config/store_layout.json")
    args = parser.parse_args()
    
    calibrate(args.clip, args.camera, args.layout)