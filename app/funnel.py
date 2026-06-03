from sqlmodel import Session, select
from models import EventRecord
from database import engine
from metrics import compute_conversion_rate

def get_funnel(store_id: str) -> dict:
    with Session(engine) as db:
        events = db.exec(
            select(EventRecord).where(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False
            )
        ).all()

    if not events:
        return {
            "store_id": store_id,
            "stages": [
                {"stage": "Entry",        "count": 0, "dropoff_pct": 0},
                {"stage": "Zone Visit",   "count": 0, "dropoff_pct": 0},
                {"stage": "Billing Zone", "count": 0, "dropoff_pct": 0},
                {"stage": "Purchase",     "count": 0, "dropoff_pct": 0},
            ],
            "session_unit": True,
            "reentry_excluded": True
        }

    sessions = {}
    for e in events:
        sid = e.session_id
        if sid not in sessions:
            sessions[sid] = {
                "visitor_id": e.visitor_id,
                "zones": set(),
                "billing": False
            }
        if e.zone_id:
            sessions[sid]["zones"].add(e.zone_id)
        if e.zone_id == "CASH_COUNTER":
            sessions[sid]["billing"] = True

    total = len(sessions)
    visited_zone = sum(1 for s in sessions.values() if s["zones"])
    reached_billing = sum(1 for s in sessions.values() if s["billing"])

    rate = compute_conversion_rate(store_id, events)
    unique_sessions = len(set(e.session_id for e in events))
    converted = round(rate * unique_sessions)

    def dropoff(a, b):
        return round((1 - a / b) * 100, 1) if b > 0 else 0

    return {
        "store_id": store_id,
        "stages": [
            {"stage": "Entry",        "count": total,
             "dropoff_pct": 0},
            {"stage": "Zone Visit",   "count": visited_zone,
             "dropoff_pct": dropoff(visited_zone, total)},
            {"stage": "Billing Zone", "count": reached_billing,
             "dropoff_pct": dropoff(reached_billing, visited_zone)},
            {"stage": "Purchase",     "count": converted,
             "dropoff_pct": dropoff(converted, reached_billing)},
        ],
        "session_unit": True,
        "reentry_excluded": True
    }