# detection_pipeline/debug_staff_hsv.py
import cv2
import numpy as np
from ultralytics import YOLO

def debug_hsv(clip_path="clips/CAM 5.mp4", frame_target=500):
    model = YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(clip_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_target)
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        print("Could not read frame")
        return
    
    results = model(frame, classes=[0], conf=0.45, verbose=False)
    print(f"Detections at frame {frame_target}: {len(results[0].boxes)}")
    
    for i, box in enumerate(results[0].boxes):
        xyxy = box.xyxy[0].cpu().numpy()
        x1, y1, x2, y2 = map(int, xyxy)
        h = y2 - y1
        cx = (x1 + x2) // 2
        cy = y2
        
        torso_y1 = y1 + h // 4
        torso_y2 = y1 + 3 * h // 4
        torso = frame[torso_y1:torso_y2, x1:x2]
        
        if torso.size == 0:
            continue
            
        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
        lower = np.array([0, 0, 0])
        upper = np.array([180, 255, 80])
        mask = cv2.inRange(hsv, lower, upper)
        ratio = np.count_nonzero(mask) / mask.size
        mean_hsv = cv2.mean(hsv)[:3]
        
        print(f"\nPerson {i+1}:")
        print(f"  Centroid (feet): ({cx}, {cy})")
        print(f"  BBox: ({x1},{y1}) → ({x2},{y2})")
        print(f"  Detection conf: {float(box.conf):.2f}")
        print(f"  Mean HSV: H={mean_hsv[0]:.0f} S={mean_hsv[1]:.0f} V={mean_hsv[2]:.0f}")
        print(f"  Black pixel ratio: {ratio:.2f}")
        print(f"  HSV says: {'STAFF' if ratio >= 0.40 else 'CUSTOMER'}")
        
        # Draw on frame
        color = (0, 0, 255) if ratio >= 0.40 else (0, 255, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, f"{'STAFF' if ratio >= 0.40 else 'CUST'} {ratio:.2f}",
                   (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cv2.circle(frame, (cx, cy), 6, (0, 255, 255), -1)
        cv2.putText(frame, f"({cx},{cy})", (cx+5, cy),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,255,255), 1)
        
        cv2.imwrite(f"calibration/torso_p{i+1}.jpg", torso)
    
    # Draw staff zone candidate
    staff_zone = [[0, 200], [450, 200], [450, 1080], [0, 1080]]
    pts = np.array(staff_zone, dtype=np.int32)
    cv2.polylines(frame, [pts], True, (255, 0, 0), 2)
    cv2.putText(frame, "STAFF ZONE", (10, 250),
               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
    
    cv2.imwrite("calibration/cam5_staff_debug.jpg", frame)
    print(f"\nSaved: calibration/cam5_staff_debug.jpg")
    print("RED box = classified STAFF, GREEN box = classified CUSTOMER")
    print("BLUE polygon = proposed staff zone")

debug_hsv(frame_target=500)