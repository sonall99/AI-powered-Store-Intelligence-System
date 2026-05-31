# detection_pipeline/emit.py
"""
Event schema builder and emitter.
Takes raw detection data, builds structured events, writes to JSONL or POSTs to API.
"""

import uuid
import json
import httpx
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class EventMetadata:
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0
    frame_number: Optional[int] = None
    clip_id: Optional[str] = None


@dataclass
class StoreEvent:
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    session_id: str
    event_type: str        # ENTRY EXIT ZONE_ENTER ZONE_EXIT ZONE_DWELL
                           # BILLING_QUEUE_JOIN BILLING_QUEUE_ABANDON REENTRY
    timestamp: str         # ISO-8601 UTC
    zone_id: Optional[str]
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata: EventMetadata = field(default_factory=EventMetadata)

    def to_dict(self):
        d = asdict(self)
        return d


def make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    session_id: str,
    event_type: str,
    timestamp_ms: int,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.9,
    frame_number: Optional[int] = None,
    clip_id: Optional[str] = None,
    session_seq: int = 0,
    queue_depth: Optional[int] = None,
) -> StoreEvent:
    
    # Convert Unix ms to ISO-8601 UTC
    ts = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
    
    return StoreEvent(
        event_id=str(uuid.uuid4()),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        session_id=session_id,
        event_type=event_type,
        timestamp=ts,
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=confidence,
        metadata=EventMetadata(
            queue_depth=queue_depth,
            session_seq=session_seq,
            frame_number=frame_number,
            clip_id=clip_id,
        )
    )


class EventEmitter:
    """
    Collects events and writes to JSONL file.
    Optionally also POSTs to API in batches.
    """
    
    def __init__(
        self,
        store_id: str,
        camera_id: str,
        output_dir: str = "events",
        api_url: Optional[str] = None,
        batch_size: int = 100,
    ):
        self.store_id = store_id
        self.camera_id = camera_id
        self.api_url = api_url
        self.batch_size = batch_size
        self.buffer = []
        
        os.makedirs(output_dir, exist_ok=True)
        safe_cam = camera_id.replace(" ", "_").replace("/", "_")
        self.output_path = os.path.join(output_dir, f"{safe_cam}_events.jsonl")
        self.file_handle = open(self.output_path, "w")
        self.total_emitted = 0
    
    def emit(self, event: StoreEvent):
        line = json.dumps(event.to_dict())
        self.file_handle.write(line + "\n")
        self.file_handle.flush()
        self.buffer.append(event.to_dict())
        self.total_emitted += 1
        
        # Auto-flush to API when buffer is full
        if self.api_url and len(self.buffer) >= self.batch_size:
            self._post_to_api()
    
    def _post_to_api(self):
        if not self.buffer:
            return
        try:
            response = httpx.post(
                f"{self.api_url}/events/ingest",
                json={"events": self.buffer},
                timeout=30.0
            )
            if response.status_code == 200:
                result = response.json()
                print(f"  API: inserted={result.get('inserted',0)} "
                      f"duplicates={result.get('duplicates',0)} "
                      f"rejected={result.get('rejected',0)}")
            else:
                print(f"  API error: {response.status_code} — {response.text[:200]}")
        except Exception as e:
            print(f"  API post failed: {e} — events saved to JSONL")
        finally:
            self.buffer.clear()
    
    def flush(self):
        """Call at end of clip processing."""
        if self.api_url and self.buffer:
            self._post_to_api()
        self.file_handle.close()
        print(f"  Emitted {self.total_emitted} events → {self.output_path}")