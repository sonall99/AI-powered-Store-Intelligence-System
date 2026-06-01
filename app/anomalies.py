from sqlmodel import Session, select
from app.models import EventRecord
from app.database import engine
from app.metrics import compute_conversion_rate

def get_anomalies(store_id: str) -> dict:
    anomalies = []

    with Session(engine) as db:
        all_customer_events = db.exec(
            select(EventRecord).where(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False
            )
        ).all()

        billing_events = [
            e for e in all_customer_events
            if e.zone_id == "CASH_COUNTER"
        ]

    # Queue spike
    queue_depth = len(set(e.visitor_id for e in billing_events))
    if queue_depth > 5:
        anomalies.append({
            "type": "BILLING_QUEUE_SPIKE",
            "severity": "CRITICAL" if queue_depth > 8 else "WARN",
            "message": f"Queue depth is {queue_depth} at billing counter.",
            "suggested_action": "Dispatch additional billing staff immediately."
        })

    # Conversion drop
    if all_customer_events:
        rate = compute_conversion_rate(store_id, all_customer_events)
        unique = len(set(e.visitor_id for e in all_customer_events))
        if unique > 3 and rate == 0.0:
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "message": "Zero conversions detected despite customer traffic.",
                "suggested_action": "Verify POS data feed and check staff at billing counter."
            })

    # Dead zone — no events in any zone for customer
    zone_ids = set(e.zone_id for e in all_customer_events if e.zone_id)
    if not zone_ids and all_customer_events:
        anomalies.append({
            "type": "DEAD_ZONE",
            "severity": "INFO",
            "message": "Customers detected but no zone activity recorded.",
            "suggested_action": "Check zone polygon configuration and camera coverage."
        })

    if not anomalies:
        anomalies.append({
            "type": "NO_ANOMALIES",
            "severity": "INFO",
            "message": "All metrics within normal range.",
            "suggested_action": "No action required."
        })

    return {"store_id": store_id, "anomalies": anomalies}