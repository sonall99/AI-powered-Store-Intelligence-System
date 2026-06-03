from sqlmodel import Session, select
from models import EventRecord
from database import engine
import json, uuid
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()

def ingest_events(payload: dict) -> dict:
    events = payload.get("events", [])
    inserted = duplicates = rejected = 0
    errors = []

    with Session(engine) as db:
        for e in events:
            try:
                existing = db.get(EventRecord, e.get("event_id", ""))
                if existing:
                    duplicates += 1
                    continue

                ts_iso = e.get("timestamp", "")
                ts_ms = int(datetime.fromisoformat(
                    ts_iso.replace("Z", "+00:00")
                ).timestamp() * 1000) if ts_iso else 0

                record = EventRecord(
                    event_id=e.get("event_id", str(uuid.uuid4())),
                    store_id=e.get("store_id", ""),
                    camera_id=e.get("camera_id", ""),
                    visitor_id=e.get("visitor_id", ""),
                    session_id=e.get("session_id", ""),
                    event_type=e.get("event_type", ""),
                    timestamp_iso=ts_iso,
                    timestamp_ms=ts_ms,
                    zone_id=e.get("zone_id"),
                    dwell_ms=e.get("dwell_ms", 0),
                    is_staff=e.get("is_staff", False),
                    confidence=e.get("confidence", 0.0),
                    raw_json=json.dumps(e)
                )
                db.add(record)
                inserted += 1
            except Exception as ex:
                rejected += 1
                errors.append({
                    "event_id": e.get("event_id"),
                    "error": str(ex)
                })

        db.commit()

    logger.info("events_ingested",
                inserted=inserted,
                duplicates=duplicates,
                rejected=rejected)

    return {
        "status": "ok" if rejected == 0 else "partial",
        "inserted": inserted,
        "duplicates": duplicates,
        "rejected": rejected,
        "errors": errors
    }