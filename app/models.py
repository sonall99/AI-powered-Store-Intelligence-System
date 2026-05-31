from sqlmodel import SQLModel, Field
from typing import Optional

class EventRecord(SQLModel, table=True):
    __tablename__ = "events"
    event_id: str = Field(primary_key=True)
    store_id: str = Field(index=True)
    camera_id: str = ""
    visitor_id: str = ""
    session_id: str = ""
    event_type: str = ""
    timestamp_iso: str = ""
    timestamp_ms: int = Field(default=0, index=True)
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 0.0
    raw_json: str = ""