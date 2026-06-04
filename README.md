# Store Intelligence System
### Purplle Tech Challenge 2026 — Round 2

A production-ready retail analytics system engineered to process raw CCTV footage, emit structured behavioral events, and compute real-time conversion metrics at the edge. 

Built to eliminate offline retail data blind spots, this system focuses strictly on the North Star metric: **Offline Store Conversion Rate**.

---

## Quick Start (5-Command Setup)

### 1. Clone the Repository

```bash
git clone https://github.com/sonall99/AI-powered-Store-Intelligence-System.git
cd AI-powered-Store-Intelligence-System
```

### 2. Build and Start Services

```bash
docker compose up --build -d
```

### 3. Verify Health

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok", "service": "store-intelligence-api", "database": "ok"}
```

### 4. Generate Events from CCTV Footage

Processes store camera footage and generates structured retail events.

```bash
python detection_pipeline/detect.py
```

### 5. Replay Events into Backend

Streams the generated events into the API for live analytics.

```bash
python detection_pipeline/replay.py
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

###  Replay Pre-generated Events (fastest, no GPU required)

```bash
python detection_pipeline/replay.py
```

This reads all `.jsonl` files from `events/` and POSTs them to the API.  
After replay, `/stores/ST1008/metrics` will return real visitor and zone data.


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
