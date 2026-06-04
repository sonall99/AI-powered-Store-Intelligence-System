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
            "visitor_unit": True,  # Changed from session_unit
            "reentry_excluded": True
        }

    visitors = {}
    for e in events:
        vid = e.visitor_id
        if vid not in visitors:
            visitors[vid] = {
                "visitor_id": e.visitor_id,
                "zones": set(),
                "billing": False
            }
        if e.zone_id:
            visitors[vid]["zones"].add(e.zone_id)
        if e.zone_id == "CASH_COUNTER":
            visitors[vid]["billing"] = True

    total = len(visitors)
    visited_zone = sum(1 for v in visitors.values() if v["zones"])
    reached_billing = sum(1 for v in visitors.values() if v["billing"])

    rate = compute_conversion_rate(store_id, events)
    unique_visitors = len(set(e.visitor_id for e in events))
    converted = round(rate * unique_visitors)

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
        "visitor_unit": True, # Changed from session_unit
        "reentry_excluded": True
    }