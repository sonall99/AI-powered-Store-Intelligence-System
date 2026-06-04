# Store Intelligence System — Architecture & Design

---

## System Architecture

This system solves a real business problem: Apex Retail has mature online analytics but zero visibility into physical store behaviour. The pipeline I built starts from raw CCTV footage and produces a live, queryable intelligence layer that answers the same questions a digital analytics platform would — unique visitors, conversion rate, zone engagement, and operational anomalies — without any customer identity or PII.

The architecture is deliberately end-to-end and single-responsibility at each stage. Raw video goes into a detection pipeline that emits structured behavioural events. Those events flow into a REST API that ingests, validates, deduplicates, and computes metrics on read. A live dashboard consumes a Server-Sent Events stream from the API and renders real-time analytics without polling. Every component is containerised and starts with a single `docker compose up`.

---

## Component Responsibilities

### Detection Pipeline

I use YOLOv8s (Ultralytics) with ByteTrack for person detection and tracking. ByteTrack ships inside the Ultralytics package — zero additional dependencies — and its low-confidence frame handling is directly relevant to the partial occlusion edge case in the footage. Staff detection uses a two-stage cascade: spatial zone check first (is this person behind the counter?), HSV uniform colour as fallback. Zone detection uses polygon containment against camera-specific coordinates stored in `store_layout.json`. All pipeline configuration — tripwire coordinates, staff uniform HSV ranges, zone polygons — is externalised into `config/store_layout.json` so the pipeline generalises to any store without code changes. The entry point is `replay.py` for batch processing, which POSTs JSONL event batches to the API.

### Event Schema and Emission

Events conform to the PDF-specified schema with one deliberate addition: `frame_number` in metadata for debuggability. I explicitly removed `session_id` from my schema — the standard design pattern includes it, but under burst ingestion in SQLite it caused write-lock contention because session state required a read-modify-write cycle per event. My visitor-centric schema eliminates this: all analytics aggregate over `visitor_id` directly, and funnel deduplication happens at query time rather than write time. This is a deliberate architectural tradeoff documented further in `CHOICES.md`.

### Analytics Engine

Metrics are computed on read from the raw events table. There is no pre-aggregation layer. `unique_visitors` counts distinct `visitor_id` values where `is_staff=false`. Conversion rate correlates billing zone dwell events with POS transaction timestamps in a 5-minute window: a visitor who was in `CASH_COUNTER` within 5 minutes before a transaction is counted as converted. Abandonment rate computes sessions that entered the billing zone but have no matching POS transaction. All metrics return structured zero values for empty or zero-traffic stores — never null, never a 404.

### Anomaly Detection

The anomaly engine is a rule-based cascade with three active detectors. `BILLING_QUEUE_SPIKE` fires when unique billing zone visitors exceed a configurable threshold (default 8) with severity scaling to CRITICAL above 12. `CONVERSION_DROP` fires when a store has customer traffic but zero conversions — this covers the cold-start case where there is no 7-day baseline by treating zero conversions as an actionable signal regardless of history. `DEAD_ZONE` fires when no customer zone events have been received in 30 minutes. Every anomaly includes a `suggested_action` string written from retail domain knowledge, not generic boilerplate.

### Dashboard

The dashboard is a single `index.html` served directly from FastAPI via `FileResponse`. It connects to a Server-Sent Events endpoint (`/stores/{id}/stream`) that pushes metric updates whenever the backend state changes. I rejected `setInterval` polling because it generates constant API load regardless of whether any new events have arrived — SSE pushes only when there is something new to send. I rejected React because it requires a build step, npm dependencies, and a separate container for what is fundamentally a read-only display. Vanilla JS with EventSource is the correct tool.

---

## Data Model

```
Raw JSONL Events
      │
      ▼
POST /events/ingest
├── Schema validation (confidence range, event_type enum, zone_id rules)
├── Idempotency check (event_id primary key)
└── INSERT INTO events table
      │
      ▼
events table
(event_id, store_id, camera_id, visitor_id,
 event_type, timestamp_ms, zone_id, dwell_ms,
 is_staff, confidence, raw_json)
      │
      ┌──────────────────────────────┐
      ▼                              ▼
GET /metrics                    GET /funnel
COUNT DISTINCT visitor_id        GROUP BY visitor_id
WHERE is_staff=false             deduplicate on visitor_id
JOIN pos_transactions            count per funnel stage
on 5-min billing window
```

---

## AI-Assisted Decisions

### Decision 1: Re-ID Approach

I asked Claude to analyse three Re-ID options: (1) full OSNet model via torchreid, (2) HSV colour histogram over spatial torso strips, (3) pure IoU-based trajectory matching.

Claude recommended OSNet for highest Re-ID accuracy. I rejected this for deployment reasons: torchreid requires a complex Docker build (3GB+ image, CUDA version dependencies, `python setup.py develop` install that fails on Apple Silicon without `--platform linux/amd64`). The colour histogram approach builds in a 200MB image, runs on any CPU, and is explainable — cosine distance on 8-strip HSV histograms takes 15 seconds to explain in a follow-up interview. I kept the interface identical to OSNet's `FeatureExtractor` so the upgrade path is a one-line swap. The failure mode I accepted is reduced Re-ID accuracy under severe lighting change — documented in Known Limitations.

### Decision 2: Removing `session_id` from Schema

The standard event schema includes `session_id` to group events into visits. I asked Claude to review my schema for funnel computation requirements. Its recommendation was to keep `session_id` as a first-class field.

I disagreed and removed it after observing a concrete failure: during burst replay of 182 events in rapid succession, SQLite's write-lock contention caused dropped insertions because each session state update required a read-modify-write cycle. Under SQLite WAL mode this is manageable at low rates but degrades under burst. My visitor-centric schema writes each event as a pure append — no read before write — which is how append-only event stores are supposed to work. Funnel deduplication moved to query time: `COUNT DISTINCT visitor_id` is O(n) and correct. The tradeoff is slightly more complex query logic. The gain is robust ingestion under any load.

### Decision 3: Dashboard Real-time Strategy

I asked Claude whether SSE or WebSocket was more appropriate for the live dashboard. It recommended WebSocket for bidirectional communication capability.

I disagreed. The dashboard is read-only. There is no client-to-server message flow. WebSocket adds handshake complexity, requires a separate ASGI handler, and provides zero benefit for a unidirectional data stream. SSE over HTTP/1.1 works through every reverse proxy, corporate firewall, and load balancer that exists. A single `EventSource` constructor in vanilla JS is the entire client implementation. I shipped this in 80 lines and it works correctly. The one limitation I accepted: SSE does not support binary frames, which matters if we ever needed to stream video thumbnails — we do not.

---

## Known Limitations

- **Entry/exit counts on Store 2:** The double-door entrance at bottom-centre of the frame required a non-standard tripwire orientation (`inside_is: bottom`). The tripwire logic now supports both orientations, but calibration is manual and the 2.3-minute clip window limits observable entries.

- **Re-ID under extreme lighting:** The HSV colour histogram Re-ID degrades when a person moves from warm spotlight (yellow cast) to ambient fluorescent (blue cast). VIS_9a3fe2 in CAM_2 exhibited `is_staff` fluctuation for this reason. I chose not to tighten the threshold because doing so would misclassify customers in dark clothing, which corrupts the conversion rate — the North Star metric.

- **CAM_3 produced zero events** for the Store 1 clip. Visual inspection confirmed this is correct: the 2.3-minute window contained no customer entries. The pipeline correctly handles zero-traffic windows.

- **CAM_4 is a stockroom camera.** All persons detected in CAM_4 are staff. This camera is excluded from customer analytics by design, not by failure.

- **POS correlation is a heuristic.** The 5-minute billing zone window correctly attributes most conversions but has a known failure mode: if multiple customers are simultaneously in the billing zone, the transaction is attributed to the one with the longest billing dwell. This is documented and acceptable for the challenge scope.

---

## Production Scaling Path

- At 40 stores × 15fps × 3 cameras, the ingest rate approaches 1,800 events/second. SQLite WAL mode handles this on a single node but becomes a bottleneck under concurrent reads. The correct upgrade is TimescaleDB (PostgreSQL extension for time-series) — not vanilla PostgreSQL, which has the same write bottleneck without the time-series optimisations.

- The detection pipeline currently runs as a batch job. At production scale, each camera would stream frames to a dedicated worker process, with events published to a Kafka topic per store. The API becomes a consumer rather than a direct ingest target.

- The anomaly detection baselines are currently rule-based with fixed thresholds. With 7+ days of data per store, these should be replaced with Z-score detection against time-of-week-adjusted rolling averages — the same approach used in production APM systems.
