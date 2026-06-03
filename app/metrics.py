from sqlmodel import Session, select
from models import EventRecord
from database import engine
from sessions import get_pos_data

def get_metrics(store_id: str) -> dict:
    with Session(engine) as db:
        all_events = db.exec(
            select(EventRecord).where(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False
            )
        ).all()

    if not all_events:
        return {
            "store_id": store_id,
            "metrics": {
                "unique_visitors": 0,
                "conversion_rate": 0.0,
                "avg_dwell_per_zone": {},
                "current_queue_depth": 0,
                "abandonment_rate": 0.0,
                "active_visitors": 0
            },
            "data_quality": {
                "session_count": 0,
                "confidence_flag": "NO_DATA"
            }
        }

    unique_visitors = len(set(e.visitor_id for e in all_events))

    zone_dwells = {}
    for e in all_events:
        if e.zone_id and e.dwell_ms > 0:
            zone_dwells.setdefault(e.zone_id, []).append(e.dwell_ms)
    avg_dwell = {z: round(sum(v)/len(v)) for z, v in zone_dwells.items()}

    conversion_rate = compute_conversion_rate(store_id, all_events)

    billing_visitors = len(set(
        e.visitor_id for e in all_events
        if e.zone_id == "CASH_COUNTER"
    ))

    return {
        "store_id": store_id,
        "metrics": {
            "unique_visitors": unique_visitors,
            "conversion_rate": round(conversion_rate, 4),
            "avg_dwell_per_zone": avg_dwell,
            "current_queue_depth": billing_visitors,
            "abandonment_rate": 0.0,
            "active_visitors": unique_visitors
        },
        "data_quality": {
            "session_count": len(set(e.session_id for e in all_events)),
            "confidence_flag": "OK" if unique_visitors >= 1 else "NO_DATA"
        }
    }


def compute_conversion_rate(store_id: str, customer_events: list) -> float:
    POS_DATA = get_pos_data()
    if not customer_events or not POS_DATA:
        return 0.0

    store_txns = [t for t in POS_DATA if t["store_id"] == store_id]
    if not store_txns:
        return 0.0

    billing_sessions = {}
    for e in customer_events:
        if e.zone_id == "CASH_COUNTER":
            if e.session_id not in billing_sessions:
                billing_sessions[e.session_id] = e.timestamp_ms

    converted_sessions = set()
    window_ms = 5 * 60 * 1000

    for txn in store_txns:
        for sid, billing_ts in billing_sessions.items():
            # Transaction must come AFTER billing zone entry
            if 0 <= txn["timestamp_ms"] - billing_ts <= window_ms:
                converted_sessions.add(sid)
                break

    unique_sessions = len(set(e.session_id for e in customer_events))
    if unique_sessions == 0:
        return 0.0

    return len(converted_sessions) / unique_sessions