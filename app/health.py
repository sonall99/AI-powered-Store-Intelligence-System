from sqlmodel import Session, select
from models import EventRecord
from database import engine
from datetime import datetime, timezone

def get_health() -> dict:
    db_status = "ok"
    try:
        with Session(engine) as db:
            db.exec(select(EventRecord).limit(1))
    except Exception:
        db_status = "error"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": "store-intelligence-api",
        "database": db_status,
        "checked_at": datetime.now(timezone.utc).isoformat()
    }