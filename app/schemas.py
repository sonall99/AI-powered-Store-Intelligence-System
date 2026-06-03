# app/schemas.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class EventMetadataSchema(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0

    class Config:
        extra = "forbid"  # Strict Check: Reject any extra fields not in PDF

class StoreEventSchema(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float
    metadata: EventMetadataSchema

    class Config:
        extra = "forbid"  # Strict Check: Reject extra fields like session_id at root

class IngestPayload(BaseModel):
    events: List[StoreEventSchema]