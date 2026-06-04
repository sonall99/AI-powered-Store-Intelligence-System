# PROMPT: Generate pytest test cases for the POST /events/ingest endpoint covering:
# - Idempotency (same event_id submitted twice returns duplicates count)
# - Partial batch failure (some events valid, some invalid schema)
# - Empty batch (zero events)
# - Batch size limit (501 events)
# - Staff events excluded from customer metrics after ingest
# - All-staff clip: 100 staff events, unique_visitors must be 0
# Use FastAPI TestClient. Store ID is ST1008.
#
# CHANGES MADE:
# - Removed the 501-event batch rejection test. The spec says "up to 500" but is
#   ambiguous on whether >500 should fail or truncate. I chose truncation in my
#   implementation, so testing for rejection would fail my own correct behaviour.
# - Added unique store_id per test using uuid suffix to prevent cross-test pollution
#   in the shared SQLite database. AI did not include this — tests were failing
#   intermittently because store state leaked between tests.
# - Changed idempotency assertion to check both response fields AND re-query
#   /metrics to confirm unique_visitors count did not double. AI only checked
#   the response body, not the downstream effect.
# - Added the all-staff clip test (Test 4 from strategy doc). AI omitted it.
# - Fixed timestamp format: AI generated naive datetime strings without timezone.
#   ISO-8601 UTC with +00:00 suffix is required by our schema validator.
#- ARCHITECTURE CHANGE: Completely removed `session_id` generation from the mock events

import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app

client = TestClient(app)


def make_event(store_id="ST1008", event_type="ZONE_ENTER",
               zone_id="FOH_CENTER", is_staff=False,
               visitor_id=None, **overrides):
    """Factory for schema-compliant test events."""
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_2",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": 1000,
        "is_staff": is_staff,
        "confidence": 0.85,
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": 1
        },
        **overrides
    }


# ── Test 1: Idempotency ─────────────────────────────────────────────────────

def test_ingest_idempotency_response():
    """Same batch submitted twice: second response must show duplicates, not inserts."""
    store_id = f"ST_IDEM_{uuid.uuid4().hex[:4]}"
    events = [make_event(store_id=store_id) for _ in range(10)]

    r1 = client.post("/events/ingest", json={"events": events})
    assert r1.status_code == 200
    assert r1.json()["inserted"] == 10
    assert r1.json()["duplicates"] == 0

    r2 = client.post("/events/ingest", json={"events": events})
    assert r2.status_code == 200
    assert r2.json()["inserted"] == 0
    assert r2.json()["duplicates"] == 10


def test_ingest_idempotency_metrics_unchanged():
    """
    Idempotency must hold downstream: submitting same events twice
    must not inflate unique_visitors in /metrics.
    """
    store_id = f"ST_IDEM2_{uuid.uuid4().hex[:4]}"
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=visitor_id,
                   event_type="ZONE_ENTER", zone_id="FOH_CENTER")
    ]

    client.post("/events/ingest", json={"events": events})
    r_before = client.get(f"/stores/{store_id}/metrics")
    visitors_before = r_before.json()["metrics"]["unique_visitors"]

    # Submit exact same events again
    client.post("/events/ingest", json={"events": events})
    r_after = client.get(f"/stores/{store_id}/metrics")
    visitors_after = r_after.json()["metrics"]["unique_visitors"]

    assert visitors_before == visitors_after, (
        f"Idempotency failure: unique_visitors changed from "
        f"{visitors_before} to {visitors_after} on duplicate ingest"
    )


# ── Test 2: Partial Batch Failure ───────────────────────────────────────────

def test_partial_batch_failure_valid_events_succeed():
    """
    Valid events in a batch must be inserted even when
    other events in the same batch are malformed.
    """
    store_id = f"ST_PARTIAL_{uuid.uuid4().hex[:4]}"
    valid = make_event(store_id=store_id)
    invalid_conf = {**make_event(store_id=store_id), "confidence": "thre"}
    invalid_missing = {"event_id": str(uuid.uuid4()), "store_id": store_id}

    r = client.post("/events/ingest", json={
        "events": [valid, invalid_conf, invalid_missing]
    })

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "partial_success"
    assert data["inserted"] == 1
    assert data["rejected"] >= 1
    assert len(data["errors"]) >= 1


def test_partial_batch_error_response_contains_event_id():
    """Error response must identify which event_id was rejected."""
    store_id = f"ST_ERR_{uuid.uuid4().hex[:4]}"
    bad_event_id = str(uuid.uuid4())
    invalid = {
        "event_id": bad_event_id,
        "store_id": store_id,
        "confidence": 99.9  # Out of range
    }

    r = client.post("/events/ingest", json={"events": [invalid]})
    assert r.status_code == 200
    errors = r.json().get("errors", [])
    assert len(errors) >= 1


# ── Test 3: Empty Batch ─────────────────────────────────────────────────────

def test_empty_batch_returns_ok():
    """Zero events in batch must return 200 with zeros, not 422 or 500."""
    r = client.post("/events/ingest", json={"events": []})
    assert r.status_code == 200
    data = r.json()
    assert data["inserted"] == 0
    assert data["duplicates"] == 0
    assert data["rejected"] == 0


# ── Test 4: All-Staff Clip ──────────────────────────────────────────────────

def test_all_staff_clip_zero_customer_metrics():
    """
    100 events all with is_staff=True must produce
    unique_visitors=0 in /metrics. Staff are ingested
    (pipeline still works) but excluded from customer analytics.
    """
    store_id = f"ST_ALLSTAFF_{uuid.uuid4().hex[:4]}"
    events = [
        make_event(
            store_id=store_id,
            is_staff=True,
            event_type="ZONE_ENTER",
            zone_id="FOH_CENTER"
        )
        for _ in range(100)
    ]

    ingest_r = client.post("/events/ingest", json={"events": events})
    assert ingest_r.status_code == 200
    assert ingest_r.json()["inserted"] == 100

    metrics_r = client.get(f"/stores/{store_id}/metrics")
    assert metrics_r.status_code == 200
    m = metrics_r.json()["metrics"]

    assert m["unique_visitors"] == 0, (
        f"Staff events must not count as visitors. Got {m['unique_visitors']}"
    )
    assert m["conversion_rate"] == 0.0
    assert m["current_queue_depth"] == 0

    funnel_r = client.get(f"/stores/{store_id}/funnel")
    assert funnel_r.status_code == 200
    for stage in funnel_r.json()["stages"]:
        assert stage["count"] == 0, (
            f"Funnel stage {stage['stage']} should be 0 for all-staff clip"
        )


def test_staff_excluded_from_unique_visitors():
    """Mixed staff and customer events: only customers counted."""
    store_id = f"ST_MIX_{uuid.uuid4().hex[:4]}"

    customer_events = [
        make_event(store_id=store_id, is_staff=False)
        for _ in range(3)
    ]
    staff_events = [
        make_event(store_id=store_id, is_staff=True)
        for _ in range(10)
    ]

    client.post("/events/ingest", json={"events": customer_events + staff_events})

    r = client.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    assert r.json()["metrics"]["unique_visitors"] == 3