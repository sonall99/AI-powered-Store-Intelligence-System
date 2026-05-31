# Run this in your terminal right now
# detection_pipeline/verify_config.py

import json
import sys

def verify_layout(path="config/store_layout.json"):
    with open(path) as f:
        layout = json.load(f)
    
    print(f"Store: {layout['store_id']} — {layout['store_name']}")
    print(f"Cameras: {len(layout['cameras'])}")
    
    for cam in layout['cameras']:
        status = "SKIP (stockroom)" if cam.get('is_stockroom') else "PROCESS"
        ts = cam.get('start_time', 'MISSING — ADD THIS')
        print(f"  {cam['camera_id']} | {cam['role']} | start: {ts} | {status}")
    
    print(f"\nZones: {len(layout['zones'])}")
    billing = [z['zone_id'] for z in layout['zones'] if z.get('is_billing')]
    print(f"  Billing zones: {billing}")
    
    staff_only = [z['zone_id'] for z in layout['zones'] if z.get('is_staff_only')]
    print(f"  Staff-only zones: {staff_only}")
    
    # Check every camera has start_time
    missing = [c['camera_id'] for c in layout['cameras'] if 'start_time' not in c]
    if missing:
        print(f"\nWARNING: Missing start_time on: {missing}")
    else:
        print(f"\nAll cameras have start_time. Good.")

verify_layout()