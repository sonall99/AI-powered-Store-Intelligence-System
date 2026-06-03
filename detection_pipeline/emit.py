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
    

@dataclass
class StoreEvent:
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str        
    timestamp: str         
    zone_id: Optional[str]
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata: EventMetadata = field(default_factory=EventMetadata)

    def to_dict(self):
        d = asdict(self)
        return d
# detection_pipeline/emit.py — add this validation function

# Required fields per PDF spec
REQUIRED_FIELDS = {
    "event_id", "store_id", "camera_id", "visitor_id",
    "event_type", "timestamp", "zone_id", "dwell_ms",
    "is_staff", "confidence", "metadata"
}

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}

def validate_event(event: StoreEvent) -> list[str]:
    violations = []
    d = event.to_dict()
    
    # 1. Check required fields
    missing = REQUIRED_FIELDS - set(d.keys())
    if missing:
        violations.append(f"Missing required fields: {missing}")
        
    # NEW 2. Strict Check: Reject Extra Root Fields
    extra_root = set(d.keys()) - REQUIRED_FIELDS
    if extra_root:
        violations.append(f"Strict Schema Violation! Extra root fields found: {extra_root}")

    # NEW 3. Strict Check: Reject Extra Metadata Fields
    ALLOWED_METADATA = {"queue_depth", "sku_zone", "session_seq"}
    meta_dict = d.get("metadata", {})
    extra_meta = set(meta_dict.keys()) - ALLOWED_METADATA
    if extra_meta:
        violations.append(f"Strict Schema Violation! Extra metadata fields found: {extra_meta}")
        
    # Validate event_type
    if d.get("event_type") not in VALID_EVENT_TYPES:
        violations.append(f"Invalid event_type: {d.get('event_type')}")
        
    # Validate confidence range
    conf = d.get("confidence", -1)
    if not (0.0 <= conf <= 1.0):
        violations.append(f"confidence {conf} out of range [0.0, 1.0]")
    
    # zone_id rules
    event_type = d.get("event_type", "")
    zone_id = d.get("zone_id")
    if event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                       "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"):
        if not zone_id:
            violations.append(f"zone_id required for {event_type}")
    if event_type in ("ENTRY", "EXIT", "REENTRY"):
        if zone_id is not None:
            violations.append(f"zone_id must be null for {event_type}, got {zone_id}")
    
    # event_id must be UUID v4 format
    import re
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    )
    if not uuid_pattern.match(d.get("event_id", "")):
        violations.append(f"event_id not UUID v4: {d.get('event_id')}")
    
    return violations

def make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    # REMOVED: session_id: str,
    event_type: str,
    timestamp_ms: int,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.9,
    # REMOVED: frame_number: Optional[int] = None,
    # REMOVED: clip_id: Optional[str] = None,
    session_seq: int = 0,
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
) -> StoreEvent:
    
    # Convert Unix ms to ISO-8601 UTC
    ts = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
    
    return StoreEvent(
        event_id=str(uuid.uuid4()),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=ts.replace("+00:00", "Z"), # Ensure 'Z' format as per PDF
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=confidence,
        metadata=EventMetadata(
            queue_depth=queue_depth,
            sku_zone=sku_zone,
            session_seq=session_seq,
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
        violations = validate_event(event)
        if violations:
            import sys
            print(f"  [SCHEMA WARNING] {event.event_id}: {violations}", file=sys.stderr)
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
        if self.api_url and self.buffer:
            self._post_to_api()
        self.file_handle.close()
        print(f"  Emitted {self.total_emitted} events → {self.output_path}")
        print(f"  Running schema compliance check...")
        self._schema_compliance_report()

    def _schema_compliance_report(self):
        """Read back emitted events and report compliance."""
        violations = 0
        total = 0
        event_ids = set()
        
        with open(self.output_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    total += 1
                    
                    # Check uniqueness
                    eid = e.get("event_id")
                    if eid in event_ids:
                        print(f"  [DUPLICATE event_id] {eid}")
                        violations += 1
                    event_ids.add(eid)
                    
                except json.JSONDecodeError as ex:
                    print(f"  [INVALID JSON] {ex}")
                    violations += 1
        
        pct = round((1 - violations/max(total,1)) * 100, 1)
        print(f"  Schema compliance: {pct}% ({total-violations}/{total} valid)")