import httpx, json, sys, os, glob

def replay(events_dir="events", api_url="http://localhost:8000"):
    files = sorted(glob.glob(os.path.join(events_dir, "*.jsonl")))
    
    if not files:
        print(f"No JSONL files found in {events_dir}/")
        return
    
    total_inserted = 0
    
    for filepath in files:
        events = []
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        
        if not events:
            print(f"  {os.path.basename(filepath)}: empty, skipping")
            continue
        
        # Send in batches of 100
        file_inserted = 0
        for i in range(0, len(events), 100):
            batch = events[i:i+100]
            try:
                r = httpx.post(
                    f"{api_url}/events/ingest",
                    json={"events": batch},
                    timeout=30
                )
                result = r.json()
                file_inserted += result.get("inserted", 0)
            except Exception as e:
                print(f"  ERROR: {e}")
        
        print(f"  {os.path.basename(filepath)}: {file_inserted}/{len(events)} inserted")
        total_inserted += file_inserted
    
    print(f"\nTotal inserted: {total_inserted}")
    print(f"Check: curl http://localhost:8000/stores/ST1008/metrics")

if __name__ == "__main__":
    api = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    replay(api_url=api)