import argparse
import os
import json
import cv2
import re
from datetime import datetime, timezone, timedelta
from ultralytics import YOLO
from store_config import load_store_layout
from tracker import VisitorTracker
from emit import EventEmitter
import traceback # Add this for better error printing

# 1. Path Management (Make it robust across OS)
try:
    import pytesseract
    # Use environment variable or fallback to standard path
    tesseract_path = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    else:
        print(f"Warning: Tesseract not found at {tesseract_path}. Burned-in timestamps will fail gracefully.")
except ImportError:
    print("Warning: pytesseract module not installed. Burned-in timestamps disabled.")
    pytesseract = None

IST_OFFSET = timedelta(hours=5, minutes=30)

def extract_burned_timestamp(frame) -> datetime | None:
    """Read CP IP Cam burned-in timestamp from top-right corner."""
    if not pytesseract:
        return None
        
    try:
        h, w = frame.shape[:2]
        region = frame[0:80, w-420:w]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
        
        # 2. Add error handling for tesseract execution
        text = pytesseract.image_to_string(
            thresh,
            config='--psm 7 -c tessedit_char_whitelist=0123456789/: '
        )
        match = re.search(r'(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2}:\d{2})', text)
        if match:
            dt_str = f"{match.group(1)} {match.group(2)}"
            dt_ist = datetime.strptime(dt_str, "%d/%m/%Y %H:%M:%S")
            dt_utc = dt_ist - IST_OFFSET
            return dt_utc.replace(tzinfo=timezone.utc)
    except Exception as e:
        # Don't crash the whole pipeline if OCR fails on one frame
        # print(f"OCR Error: {e}") 
        pass
    return None

def get_clip_start_time(video_path: str, camera_config: dict) -> datetime:
    """Get clip start time. Priority: burned-in > config > fallback to default."""
    
    # Try burned-in timestamp from first few frames
    try:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS)
            # 3. Limit the search to prevent hanging on corrupted videos
            max_frames_to_check = min(int(fps * 2), 60) 
            
            for frame_idx in range(max_frames_to_check): 
                ret, frame = cap.read()
                if not ret:
                    break
                ts = extract_burned_timestamp(frame)
                if ts:
                    cap.release()
                    print(f"  [Time] Extracted from video OCR: {ts.isoformat()}")
                    return ts
            cap.release()
    except Exception as e:
         print(f"  [Warning] Video processing for OCR failed: {e}")

    # Use config start_time (already stored as IST, convert to UTC)
    if "start_time" in camera_config:
        try:
            dt_ist = datetime.fromisoformat(camera_config["start_time"])
            dt_utc = dt_ist - IST_OFFSET
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
            print(f"  [Time] Loaded from config: {dt_utc.isoformat()}")
            return dt_utc
        except ValueError:
             print(f"  [Warning] Invalid start_time format in config: {camera_config['start_time']}")

    # 4. Final Fallback - Don't crash, use a sensible default for hackathon
    fallback_time = datetime(2026, 4, 10, 8, 0, 0, tzinfo=timezone.utc)
    print(f"  [Time] WARNING: Using fallback start time: {fallback_time.isoformat()}")
    return fallback_time

def process_camera(clip_path, camera_config, store_layout, model, args):
    camera_id = camera_config["camera_id"]
    
    # Skip stockroom
    if camera_config.get("is_stockroom"):
        print(f"  Skipping {camera_id} — stockroom camera")
        return

    # 5. Robust Video Opening
    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"  [ERROR] Failed to open video: {clip_path}")
        return
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 15.0 # Fallback if OpenCV fails to read metadata
        print(f"  [Warning] Invalid FPS detected. Defaulting to {fps}")
        
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    start_time = get_clip_start_time(clip_path, camera_config)
    print(f"  Start: {start_time.isoformat()} UTC")
    print(f"  Frames: {total_frames} @ {fps:.2f}fps")
    
    tracker = VisitorTracker(
        store_layout=store_layout,
        camera_config=camera_config,
        clip_start_time=start_time,
        fps=fps,
    )
    
    emitter = EventEmitter(
        store_id=store_layout["store_id"],
        camera_id=camera_id,
        output_dir=args.events_dir,
        api_url=args.api,
    )
    
    start_ms = int(start_time.timestamp() * 1000)
    
    # 6. Error handling around Ultralytics track
    try:
        results = model.track(
            source=clip_path,
            stream=True,
            persist=True,
            classes=[0],       # person only
            conf=args.conf,
            iou=0.7,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        
        frame_idx = 0
        for result in results:
            timestamp_ms = start_ms + int((frame_idx / fps) * 1000)
            events = tracker.process_frame(result, frame_idx, timestamp_ms)
            for event in events:
                emitter.emit(event)
            frame_idx += 1
            
            if frame_idx % 300 == 0:
                pct = (frame_idx / total_frames) * 100 if total_frames > 0 else 0
                print(f"  Progress: {pct:.0f}% ({frame_idx}/{total_frames} frames) - Active IDs: {len(tracker.active_tracks)}")
        
    except Exception as e:
        print(f"  [CRITICAL ERROR] Pipeline crashed on {camera_id}: {e}")
        traceback.print_exc() # Print full stack trace for debugging
    finally:
        # Ensure events are written even if crashed halfway
        emitter.flush()
        print(f"  [Done] Emitted events for {camera_id}")

def print_detection_summary(events_dir: str):
    """Print summary that evaluator sees when reviewing output."""
    import glob
    from collections import Counter

    all_events = []
    for f in glob.glob(os.path.join(events_dir, "*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        all_events.append(json.loads(line))
                    except:
                        pass

    if not all_events:
        print("No events generated.")
        return

    types = Counter(e["event_type"] for e in all_events)
    cameras = Counter(e["camera_id"] for e in all_events)
    staff = sum(1 for e in all_events if e["is_staff"])
    customers = len(all_events) - staff
    unique_visitors = len(set(
        e["visitor_id"] for e in all_events if not e["is_staff"]
    ))

    print(f"\n{'='*50}")
    print(f"DETECTION SUMMARY")
    print(f"{'='*50}")
    print(f"Total events    : {len(all_events)}")
    print(f"Unique customers: {unique_visitors}")
    print(f"Staff events    : {staff} ({round(staff/len(all_events)*100)}%)")
    print(f"Customer events : {customers} ({round(customers/len(all_events)*100)}%)")
    print(f"\nBy event type:")
    for t, count in types.most_common():
        print(f"  {t:<30} {count}")
    print(f"\nBy camera:")
    for c, count in cameras.most_common():
        print(f"  {c:<20} {count}")
    print(f"{'='*50}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera",     help="Process single camera e.g. 'CAM 5.mp4'")
    # Use os.path.join for default paths
    parser.add_argument("--layout",     default=os.getenv("STORE_LAYOUT_PATH", os.path.join("config", "store_layout.json")))
    parser.add_argument("--clips-dir",  default=os.getenv("CLIPS_DIR", "clips"))
    parser.add_argument("--events-dir", default=os.getenv("EVENTS_DIR", "events"))
    parser.add_argument("--api",        default=os.getenv("API_URL"))
    parser.add_argument("--model",      default="yolov8n.pt") # CHANGED TO yolov8n.pt FOR SPEED (Optional, change back to 's' if you have GPU)
    parser.add_argument("--conf",       type=float, default=0.45) # CHANGED DEFAULT TO 0.45 (Crucial Fix #3)
    args = parser.parse_args()
    
    os.makedirs(args.events_dir, exist_ok=True)
    
    try:
        layout = load_store_layout(args.layout)
    except Exception as e:
         print(f"Failed to load layout from {args.layout}: {e}")
         return

    print(f"Store: {layout['store_id']} — {layout.get('store_name', 'Unknown')}")
    
    print(f"Loading Model: {args.model}...")
    model = YOLO(args.model)
    
    cameras = layout.get("cameras", [])
    if args.camera:
        cameras = [c for c in cameras if c.get("filename") == args.camera]
        if not cameras:
            available = [c.get("filename") for c in layout.get("cameras", [])]
            print(f"Camera '{args.camera}' not found. Available: {available}")
            return
    
    # Process in priority order: entry first, billing second, floor third
    role_order = {"entry_exit": 0, "billing": 1, "main_floor_makeup": 2,
                  "skincare_zone": 3, "stockroom": 99}
    cameras = sorted(cameras, key=lambda c: role_order.get(c.get("role", ""), 50))
    
    for camera_config in cameras:
        # Use os.path.join to handle slashes correctly on Windows vs Linux
        clip_path = os.path.join(args.clips_dir, camera_config.get("filename", ""))
        
        if not os.path.exists(clip_path):
            print(f"\nSKIP: {clip_path} not found")
            continue
        
        print(f"\n{'='*50}")
        print(f"Processing: {camera_config.get('filename')} ({camera_config.get('role')})")
        process_camera(clip_path, camera_config, layout, model, args)
    
    print(f"\n{'='*50}")
    print(f"Pipeline Complete. Events written to {args.events_dir}/")
    print(f"Next step: python detection_pipeline/replay.py --source {args.events_dir}")

    print_detection_summary(args.events_dir)

if __name__ == "__main__":
    main()