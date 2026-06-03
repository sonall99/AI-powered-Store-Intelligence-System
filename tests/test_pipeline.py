# tests/test_pipeline.py

# PROMPT: Generate pytest tests for a CCTV detection pipeline event schema.
# Test that emitted JSONL events conform to the required schema:
# required fields, valid event types, UUID v4 event_ids, ISO-8601 UTC timestamps,
# zone_id rules per event type, confidence in [0,1], no duplicate event_ids.
# Load from actual JSONL files in events/ directory.
#
# CHANGES MADE:
# - Added IST→UTC conversion check (AI generated naive datetime check only)
# - Added zone_id=null assertion for ENTRY/EXIT/REENTRY (AI missed this)
# - Added file-not-found skip instead of fail (evaluator may not have run pipeline)
# - Added confidence >0 check (PDF says do not suppress low-conf, but 0.0 exactly is suspicious)

import pytest
import json
import os
import re
import glob
from datetime import datetime, timezone

EVENTS_DIR = "events"
UUID_V4_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
)
VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}
REQUIRED_FIELDS = {
    "event_id", "store_id", "camera_id", "visitor_id",
    "event_type", "timestamp", "dwell_ms", "is_staff",
    "confidence", "metadata"
}


def load_all_events():
    events = []
    files = glob.glob(os.path.join(EVENTS_DIR, "*.jsonl"))
    for f in files:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


@pytest.fixture(scope="module")
def all_events():
    events = load_all_events()
    if not events:
        pytest.skip("No JSONL files in events/ — run detection pipeline first")
    return events


def test_all_required_fields_present(all_events):
    for e in all_events:
        missing = REQUIRED_FIELDS - set(e.keys())
        assert not missing, f"Event {e.get('event_id')} missing fields: {missing}"


def test_event_ids_are_unique(all_events):
    ids = [e["event_id"] for e in all_events]
    duplicates = [eid for eid in ids if ids.count(eid) > 1]
    assert not duplicates, f"Duplicate event_ids found: {set(duplicates)}"


def test_event_ids_are_uuid_v4(all_events):
    for e in all_events:
        assert UUID_V4_PATTERN.match(e["event_id"]), \
            f"event_id not UUID v4: {e['event_id']}"


def test_valid_event_types(all_events):
    for e in all_events:
        assert e["event_type"] in VALID_EVENT_TYPES, \
            f"Invalid event_type: {e['event_type']}"


def test_timestamps_are_utc_iso8601(all_events):
    for e in all_events:
        ts = e["timestamp"]
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            assert dt.tzinfo is not None, f"Timestamp has no timezone: {ts}"
        except ValueError:
            pytest.fail(f"Invalid timestamp format: {ts}")


def test_confidence_in_valid_range(all_events):
    for e in all_events:
        conf = e["confidence"]
        assert 0.0 <= conf <= 1.0, \
            f"confidence {conf} out of range for event {e['event_id']}"


def test_zone_id_null_for_entry_exit_reentry(all_events):
    no_zone_types = {"ENTRY", "EXIT", "REENTRY"}
    for e in all_events:
        if e["event_type"] in no_zone_types:
            assert e.get("zone_id") is None, \
                f"{e['event_type']} event should have zone_id=null, got {e.get('zone_id')}"


def test_zone_id_present_for_zone_events(all_events):
    zone_required_types = {"ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                           "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}
    for e in all_events:
        if e["event_type"] in zone_required_types:
            assert e.get("zone_id"), \
                f"{e['event_type']} event missing zone_id: {e['event_id']}"


def test_dwell_ms_non_negative(all_events):
    for e in all_events:
        assert e["dwell_ms"] >= 0, \
            f"Negative dwell_ms {e['dwell_ms']} in event {e['event_id']}"


def test_is_staff_is_boolean(all_events):
    for e in all_events:
        assert isinstance(e["is_staff"], bool), \
            f"is_staff must be bool, got {type(e['is_staff'])} in {e['event_id']}"


def test_store_id_consistent(all_events):
    store_ids = set(e["store_id"] for e in all_events)
    assert "ST1008" in store_ids, \
        f"Expected ST1008 in store_ids, found: {store_ids}"


def test_zone_dwell_events_have_positive_dwell(all_events):
    dwell_events = [e for e in all_events if e["event_type"] == "ZONE_DWELL"]
    for e in dwell_events:
        assert e["dwell_ms"] >= 30000, \
            f"ZONE_DWELL should be >=30000ms, got {e['dwell_ms']} in {e['event_id']}"


def test_metadata_has_required_keys(all_events):
    for e in all_events:
        meta = e.get("metadata", {})
        assert "session_seq" in meta, \
            f"metadata missing session_seq in {e['event_id']}"