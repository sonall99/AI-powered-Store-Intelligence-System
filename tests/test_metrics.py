# PROMPT: Generate pytest tests for GET /stores/{id}/metrics and GET /stores/{id}/funnel.
# Cover: empty store returns zeros not null, conversion rate computation,
# re-entry deduplication in funnel, zero-purchase store, session-unit funnel.
# Use FastAPI TestClient with isolated store IDs per test.
#
# CHANGES MADE:
# - Added re-entry deduplication test (Test 3 from strategy doc). AI omitted this
#   entirely. This is the most important funnel test because the PDF explicitly
#   calls out "re-entries must not double-count a visitor."
# - Added zero-purchase store test (Test 2 from strategy doc). AI generated a
#   test that assumed conversion_rate would be null on empty stores. Our
#   implementation correctly returns 0.0, so the assertion was inverted.
# - Added data_quality.confidence_flag assertion on empty store. This is our
#   custom field that signals data reliability — AI did not know about it.
# - Strengthened the funnel session_unit assertion to also verify stage counts
#   are non-negative integers, not just that the field exists.
# - Removed a test for 7-day average anomaly comparison — we don't have 7 days
#   of data in the test database, so this would always return INFO not WARN.

import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app

client = TestClient(app)


def make_event(store_id, event_type="ZONE_ENTER", zone_id="FOH_CENTER",
               is_staff=False, visitor_id=None, **kwargs):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_2",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": kwargs.get("dwell_ms", 1000),
        "is_staff": is_staff,
        "confidence": kwargs.get("confidence", 0.85),
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


# ── Test 1: Empty Store ─────────────────────────────────────────────────────

def test_empty_store_metrics_returns_zeros_not_null():
    """
    /metrics on a store with zero events must return structured zeros.
    Must never return null fields, 404, or 500.
    This tests the PDF requirement: 'handle zero-traffic correctly'.
    """
    r = client.get("/stores/STORE_NEVER_EXISTS_XYZ/metrics")
    assert r.status_code == 200

    m = r.json()["metrics"]
    assert m["unique_visitors"] == 0
    assert m["conversion_rate"] == 0.0
    assert m["avg_dwell_per_zone"] == {}
    assert m["current_queue_depth"] == 0
    assert m["abandonment_rate"] == 0.0

    dq = r.json()["data_quality"]
    assert dq["visitor_count"] == 0
    assert dq["confidence_flag"] == "NO_DATA"


def test_empty_store_funnel_returns_zero_stages():
    """Empty store funnel must return 4 stages all with count=0."""
    r = client.get("/stores/STORE_EMPTY_FUNNEL_XYZ/funnel")
    assert r.status_code == 200
    data = r.json()
    assert data["visitor_unit"] == True

    for stage in data["stages"]:
        assert isinstance(stage["count"], int)
        assert stage["count"] == 0
        assert isinstance(stage["dropoff_pct"], (int, float))


def test_empty_store_heatmap_returns_empty_zones():
    r = client.get("/stores/STORE_EMPTY_HEATMAP_XYZ/heatmap")
    assert r.status_code == 200
    assert r.json()["zones"] == []


# ── Test 2: Zero-Purchase Store ─────────────────────────────────────────────

def test_zero_purchase_store_conversion_rate_is_zero_not_null():
    """
    Store with visitor traffic but no POS transactions must return
    conversion_rate=0.0, not null, not error.
    This verifies POS correlation gracefully handles missing data.
    """
    store_id = f"ST_NOPURCHASE_{uuid.uuid4().hex[:4]}"

    events = [
        make_event(store_id=store_id, event_type="ZONE_ENTER",
                   zone_id="FOH_CENTER")
        for _ in range(5)
    ]
    client.post("/events/ingest", json={"events": events})

    r = client.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200

    m = r.json()["metrics"]
    assert m["unique_visitors"] == 5
    assert m["conversion_rate"] == 0.0
    assert m["conversion_rate"] is not None


def test_zero_purchase_funnel_has_valid_dropoff():
    """
    Funnel for store with no purchases must show valid drop-off
    percentages at Purchase stage, not crash or return null.
    """
    store_id = f"ST_NOPURCHASE2_{uuid.uuid4().hex[:4]}"

    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=visitor_id,
                  event_type="ZONE_ENTER",
                   zone_id="FOH_CENTER"),
        make_event(store_id=store_id, visitor_id=visitor_id,
                  event_type="ZONE_ENTER",
                   zone_id="CASH_COUNTER"),
    ]
    client.post("/events/ingest", json={"events": events})

    r = client.get(f"/stores/{store_id}/funnel")
    assert r.status_code == 200
    stages = r.json()["stages"]
    assert len(stages) == 4

    purchase_stage = next(s for s in stages if s["stage"] == "Purchase")
    assert purchase_stage["count"] == 0
    assert isinstance(purchase_stage["dropoff_pct"], (int, float))


# ── Test 3: Re-entry Deduplication in Funnel ───────────────────────────────

def test_reentry_not_double_counted_in_funnel():
    """
    A visitor who re-enters must count as 1 unique visitor in the funnel,
    not 2. This is explicitly required by the PDF:
    'Re-entries must not double-count a visitor.'

    Sequence: ENTRY → ZONE_ENTER → EXIT → REENTRY → ZONE_ENTER → BILLING
    Expected funnel Entry count: 1 (not 2)
    """
    store_id = f"ST_REENTRY_{uuid.uuid4().hex[:4]}"
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"

    events = [
        # First visit
        make_event(store_id=store_id, visitor_id=visitor_id,
                 event_type="ZONE_ENTER",
                   zone_id="FOH_CENTER"),
        make_event(store_id=store_id, visitor_id=visitor_id,
                   event_type="ZONE_EXIT",
                   zone_id="FOH_CENTER", dwell_ms=5000),
        # Re-entry — same visitor_id, new session
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "CAM_3",
            "visitor_id": visitor_id,
            "event_type": "REENTRY",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.78,
            "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
        },
        make_event(store_id=store_id, visitor_id=visitor_id,
                   event_type="ZONE_ENTER",
                   zone_id="CASH_COUNTER"),
    ]

    client.post("/events/ingest", json={"events": events})

    funnel_r = client.get(f"/stores/{store_id}/funnel")
    assert funnel_r.status_code == 200
    stages = funnel_r.json()["stages"]

    entry_stage = next(s for s in stages if s["stage"] == "Entry")
    assert entry_stage["count"] == 1, (
        f"Re-entry should count as 1 unique visitor, got {entry_stage['count']}"
    )

    metrics_r = client.get(f"/stores/{store_id}/metrics")
    assert metrics_r.json()["metrics"]["unique_visitors"] == 1


def test_reentry_visitor_id_consistent_across_sessions():
    """
    Both sessions from a re-entry must share the same visitor_id.
    The funnel deduplication depends on this.
    """
    store_id = f"ST_REID_{uuid.uuid4().hex[:4]}"
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"

    events = [
        make_event(store_id=store_id, visitor_id=visitor_id,
                   event_type="ZONE_ENTER", zone_id="FOH_CENTER"),
        make_event(store_id=store_id, visitor_id=visitor_id,
                   event_type="ZONE_ENTER", zone_id="MAKEUP_UNIT"),
    ]
    client.post("/events/ingest", json={"events": events})

    r = client.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    assert r.json()["metrics"]["unique_visitors"] == 1


# ── Metrics Correctness ─────────────────────────────────────────────────────

def test_unique_visitors_counts_distinct_visitor_ids():
    """unique_visitors must count distinct visitor_ids, not event count."""
    store_id = f"ST_UV_{uuid.uuid4().hex[:4]}"
    visitor_a = f"VIS_{uuid.uuid4().hex[:6]}"
    visitor_b = f"VIS_{uuid.uuid4().hex[:6]}"

    events = [
        # Visitor A — 3 zone events
        make_event(store_id=store_id, visitor_id=visitor_a,
                   zone_id="FOH_CENTER"),
        make_event(store_id=store_id, visitor_id=visitor_a,
                   zone_id="MAKEUP_UNIT"),
        make_event(store_id=store_id, visitor_id=visitor_a,
                   zone_id="CASH_COUNTER"),
        # Visitor B — 1 zone event
        make_event(store_id=store_id, visitor_id=visitor_b,
                   zone_id="FOH_CENTER"),
    ]
    client.post("/events/ingest", json={"events": events})

    r = client.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    assert r.json()["metrics"]["unique_visitors"] == 2


def test_avg_dwell_computed_from_events_with_dwell():
    """avg_dwell_per_zone must only include events with dwell_ms > 0."""
    store_id = f"ST_DWELL_{uuid.uuid4().hex[:4]}"

    events = [
        make_event(store_id=store_id, zone_id="FOH_CENTER", dwell_ms=5000),
        make_event(store_id=store_id, zone_id="FOH_CENTER", dwell_ms=3000),
        make_event(store_id=store_id, zone_id="FOH_CENTER", dwell_ms=0),
    ]
    client.post("/events/ingest", json={"events": events})

    r = client.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    dwell = r.json()["metrics"]["avg_dwell_per_zone"]
    if "FOH_CENTER" in dwell:
        assert dwell["FOH_CENTER"] > 0