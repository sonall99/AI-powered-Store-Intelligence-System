# detection_pipeline/diagnose_events.py
import json
from collections import defaultdict

def diagnose(path="events/CAM_2_events.jsonl"):
    events = []
    with open(path) as f:
        for line in f:
            events.append(json.loads(line))
    
    print(f"Total events: {len(events)}")
    
    # Unique visitors
    visitors = defaultdict(list)
    for e in events:
        visitors[e['visitor_id']].append(e)
    print(f"Unique visitor_ids: {len(visitors)}")
    
    # Sessions per visitor
    sessions_per_visitor = {}
    for vid, evts in visitors.items():
        sessions = set(e['session_id'] for e in evts)
        sessions_per_visitor[vid] = len(sessions)
    
    avg_sessions = sum(sessions_per_visitor.values()) / len(sessions_per_visitor)
    max_sessions = max(sessions_per_visitor.values())
    print(f"Avg sessions per visitor: {avg_sessions:.1f}")
    print(f"Max sessions for one visitor: {max_sessions}")
    
    # Staff vs customer
    staff = [e for e in events if e['is_staff']]
    customers = [e for e in events if not e['is_staff']]
    print(f"Staff events: {len(staff)} ({len(staff)/len(events)*100:.0f}%)")
    print(f"Customer events: {len(customers)} ({len(customers)/len(events)*100:.0f}%)")
    
    # Event types
    from collections import Counter
    types = Counter(e['event_type'] for e in events)
    print(f"\nEvent type breakdown:")
    for t, count in types.most_common():
        print(f"  {t}: {count}")
    
    # Confidence distribution
    confs = [e['confidence'] for e in events]
    low_conf = [c for c in confs if c < 0.4]
    print(f"\nLow confidence (<0.4): {len(low_conf)} ({len(low_conf)/len(confs)*100:.0f}%)")
    
    # REENTRY events on this non-entry camera (should be 0 ideally)
    reentries = [e for e in events if e['event_type'] == 'REENTRY']
    print(f"REENTRY events: {len(reentries)} (should be ~0 on billing camera)")

diagnose()