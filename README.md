# Store Intelligence System
### Purplle Tech Challenge 2026 — Round 2

AI-powered retail analytics pipeline. Processes CCTV footage to produce real-time store metrics: visitor counts, conversion rate, zone heatmap, and operational anomalies. Built for Brigade Road Bangalore (ST1008) and Store 2 with generalised multi-store architecture.

---

## Quick Start — 5 Commands

```bash
git clone https://github.com/sonall99/AI-powered-Store-Intelligence-System cd AI-powered-Store-Intelligence-System
docker compose up --build
open http://localhost:8000/dashboard
```

The API starts at `http://localhost:8000`.  
The live dashboard starts at `http://localhost:8000/dashboard`.  
No manual steps beyond these five commands.

---

## Verify The System Is Running

```bash
curl http://localhost:8000/health
curl http://localhost:8000/stores/ST1008/metrics
```

Expected health response:
```json
{
  "status": "ok",
  "database": "ok",
  "stores": {},
  "checked_at": "2026-04-10T14:40:00+00:00"
}
```

---

## Loading Detection Events Into The API

### Option A — Replay Pre-generated Events (fastest, no GPU required)

```bash
python detection_pipeline/replay.py
```

This reads all `.jsonl` files from `events/` and POSTs them to the API.  
After replay, `/stores/ST1008/metrics` will return real visitor and zone data.

### Option B — Run the Full Detection Pipeline on Video Clips

```bash
# Place your MP4 files in clips/
# Store 1: CAM 1.mp4, CAM 2.mp4, CAM 3.mp4, CAM 4.mp4, CAM 5.mp4
# Store 2: entry 1.mp4, floor.mp4, billing.mp4

pip install -r requirements.txt

# Process Store 1
python detection_pipeline/detect.py \
    --layout config/store_layout.json \
    --events-dir events/store1

# Process Store 2
python detection_pipeline/detect.py \
    --layout config/store_layout_store2.json \
    --events-dir events/store2

# Load all events into the running API
python detection_pipeline/replay.py --source events/store1
python detection_pipeline/replay.py --source events/store2
```

### Option C — Docker Pipeline (detection inside container)

```bash
docker compose --profile detection run pipeline
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | System status, per-store feed lag, STALE_FEED warning |
| POST | `/events/ingest` | Batch ingest up to 500 events, idempotent by `event_id` |
| GET | `/stores/{id}/metrics` | Unique visitors, conversion rate, zone dwell, queue depth |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase with drop-off % |
| GET | `/stores/{id}/heatmap` | Zone visit frequency normalised 0–100 |
| GET | `/stores/{id}/anomalies` | Active anomalies with severity and suggested action |
| GET | `/dashboard` | Live analytics dashboard (SSE-powered, no polling) |

**Store IDs in this dataset:**
- `ST1008` — Brigade Road, Bangalore
- `ST1009` — Store 2

---

## Running Tests

```bash
pip install pytest pytest-cov
pytest tests/ -v --cov=app --cov-report=term-missing
```

Expected: all tests pass, coverage ≥ 75%.

**Test files:**
- `tests/test_ingestion.py` — Idempotency, partial failure, all-staff clip
- `tests/test_metrics.py` — Empty store, zero-purchase, re-entry deduplication
- `tests/test_anomalies.py` — Queue spike, conversion drop, health endpoint
- `tests/test_pipeline.py` — Schema compliance on emitted JSONL events

---

## Calibrating For New Store Footage

If running on a store not covered by the provided config files:

```bash
# Step 1: Inspect clips to get fps, resolution, duration
python detection_pipeline/inspect_clips.py --clips-dir clips/newstore

# Step 2: Calibrate staff uniform colour (click on uniform in frame)
python detection_pipeline/calibrate_staff_hsv.py \
    --clip clips/newstore/floor.mp4

# Step 3: Set entry tripwire (click two points on door threshold)
python detection_pipeline/calibrate.py \
    --clip clips/newstore/entry.mp4 \
    --camera CAM_ENTRY \
    --layout config/store_layout_newstore.json
```

Calibration outputs update `store_layout.json` automatically.

---

## Architecture

```
MP4 Clips
      │
      ▼
detect.py (YOLOv8s + ByteTrack)
  ├── Staff detection: spatial zone + HSV cascade
  ├── Zone detection: polygon containment
  └── Re-ID: HSV histogram cosine similarity
      │
      ▼
events/*.jsonl
      │
      ▼
replay.py → POST /events/ingest
      │
      ▼
SQLite (WAL mode)
  ├── GET /metrics    (read-time computation)
  ├── GET /funnel     (visitor_id deduplication)
  ├── GET /heatmap    (normalised zone scores)
  ├── GET /anomalies  (rule-based detection)
  └── GET /stream     (SSE push to dashboard)
      │
      ▼
dashboard/index.html
(EventSource, no polling)
```

Full architecture documentation: [`docs/DESIGN.md`](docs/DESIGN.md)  
Decision rationale: [`docs/CHOICES.md`](docs/CHOICES.md)

---

## Configuration

All store-specific configuration is in `config/`:

```
config/
├── store_layout.json           # Store 1 — Brigade Road
└── store_layout_store2.json    # Store 2
```

Each file contains: zone polygons, camera roles, tripwire coordinates, staff uniform HSV range, POS correlation window, and re-entry window. The detection pipeline reads these at runtime — no code changes needed for a new store.

---

## Known Limitations

- **Entry/exit counting** requires the entry camera clip to contain actual threshold crossings. The Store 1 CAM_3 clip covers an empty 2.3-minute window — this is correct, not a bug.
- **Staff detection** is calibrated per-store. Store 1 = black uniform, Store 2 = pink uniform. HSV thresholds are in the respective layout files.
- **POS correlation** uses a 5-minute billing zone window. Multiple simultaneous billing visitors are attributed by longest dwell time.

---

## Submission Checklist

- [x] `docker compose up --build` confirmed working
- [x] `POST /events/ingest` accepts events without 5xx
- [x] `GET /stores/ST1008/metrics` returns valid JSON
- [x] `DESIGN.md` present and non-trivial (>250 words)
- [x] `CHOICES.md` covers model selection, schema design, API architecture
- [x] Prompt blocks at top of all test files
- [x] Dashboard live at `http://localhost:8000/dashboard`
- [x] README explains detection pipeline execution in 5 commands
