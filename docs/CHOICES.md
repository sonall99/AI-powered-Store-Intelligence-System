# Architectural Decisions — Store Intelligence System

This document records three architectural decisions I made during implementation, including the options I considered, what AI tools suggested, and where I disagreed with those suggestions and why.

---

## Decision 1: Detection Model Selection

**The decision:** Which object detection model to use for person detection across 5 retail CCTV cameras at 1080p.

### Options Evaluated

**YOLOv8 (Ultralytics):** Runs at ~40fps on CPU at 640px inference resolution. ByteTrack is built into the Ultralytics package — `model.track()` gives me detection and tracking in one call with zero additional dependencies. Trained on COCO with strong person class performance. The relevant weakness is anchor-based detection, which underperforms on heavily occluded or partially cropped persons compared to attention-based models.

**RT-DETR:** Transformer attention mechanism handles partial occlusion significantly better than anchor-based detectors. I tested it on 2 minutes of CAM_5 billing area footage and saw ~8% better recall on crowded frames. The problem is inference speed: 400ms per frame on CPU versus 65ms for YOLOv8n. At 25fps source material, 400ms means I cannot process in real-time without GPU.

**MediaPipe Pose:** Extremely fast (200fps on CPU) but designed for close-range, single-person pose estimation. Retail CCTV is overhead or angled, multi-person, with significant occlusion. MediaPipe's accuracy degrades severely in this configuration — I rejected it after a 10-minute evaluation.

### What AI Suggested

AI recommended RT-DETR for the billing area camera specifically, citing the occlusion advantage. I agreed with the reasoning but disagreed with the implementation: RT-DETR requires a GPU to be practical in real-time, and the challenge requirement is `docker compose up` on a reviewer's laptop. I cannot guarantee GPU availability.

### My Decision

YOLOv8s for all batch processing (20fps on CPU, acceptable for offline clip analysis). YOLOv8n as a configurable flag (`--model yolov8n.pt`) for real-time simulation, where speed is prioritised over accuracy. I document RT-DETR as the production upgrade path for the billing camera when GPU is available.

---

## Decision 2: Event Schema — Removing `session_id`

**The decision:** Whether to include `session_id` as a first-class field in the event schema, grouping all events from a single visit into a named session.

### The Standard Design

Every event-sourcing system I have worked on includes a session identifier. The PDF schema does not specify it, but the conventional implementation would add it to enable O(1) session lookup rather than O(n) `visitor_id` scan.

### What AI Suggested

I asked AI to review my event schema for funnel computation requirements. It recommended adding `session_id` as a first-class field, arguing that computing session-level funnel stages from raw events requires complex window functions. I agreed with this reasoning initially and implemented it.

### Why I Reversed This Decision

During load testing with burst replay of 182 events, I observed SQLite write-lock contention. The cause was the session state machine: each event required a read (does session exist?) before write (insert or update). Under concurrent burst ingestion, this read-before-write pattern hits SQLite's serialisation bottleneck. Events were being dropped.

I rebuilt the schema as pure append-only: each event is self-contained with `visitor_id`, `event_type`, `zone_id`, and `timestamp_ms`. No session state is maintained at write time. Funnel deduplication is `COUNT DISTINCT visitor_id WHERE event_type IN (...)` — O(n) but correct and lock-free. The ingestion endpoint became a pure INSERT with idempotency checked only on `event_id`, which is a primary key lookup — constant time, no contention.

### What I Also Rejected

AI suggested adding a `device_id` field for IoT sensor compatibility, anticipating future integration with footfall counters. I rejected this as out of scope. The challenge asks for CCTV-derived analytics, not IoT sensor fusion. Adding fields for hypothetical future requirements is premature optimisation in a timed challenge.

### Tradeoff Accepted

Funnel computation is slightly more complex at query time. The gain is robust ingestion under any load pattern.

---

## Decision 3: API Architecture — SQLite vs PostgreSQL and Metric Computation Timing

**The decision:** Two coupled choices — which database engine, and whether to pre-compute metrics on write or compute on read.

### Database Choice

I asked AI to analyse the SQLite vs PostgreSQL tradeoff. It recommended PostgreSQL, citing ACID compliance, better concurrent write performance, and production credibility.

I disagreed for three concrete reasons:

**First,** the challenge explicitly says "SQLite is fine" in the FAQ. Using PostgreSQL adds a second Docker container, network configuration between containers, connection pooling complexity, and a larger Docker image — all of which increase the probability of `docker compose up` failing on the reviewer's machine.

**Second,** AI's concurrency argument is valid at scale (40 stores × 15fps × 3 cameras = ~1,800 events/second would exceed SQLite's write throughput) but is not relevant for a single-store demo workload. The correct response to that scaling question is in `DESIGN.md`: I would switch to TimescaleDB, not vanilla PostgreSQL, because TimescaleDB's hypertable partitioning is specifically optimised for time-series event data.

**Third,** SQLite WAL mode with `PRAGMA synchronous=NORMAL` gives me serialisable reads with concurrent writers on a single node, which is all I need.

### Metric Computation Timing

I compute metrics on read rather than pre-aggregating on write. AI suggested pre-aggregation via a background worker to reduce read latency.

I disagreed for the challenge scope: pre-aggregation requires a background worker process, a dirty-flag mechanism, and cache invalidation logic — three additional failure modes. For the event volumes in this challenge (182 events across 5 cameras), read-time computation is under 5ms. The complexity cost of pre-aggregation is not justified by the latency gain.

In production at 40 stores I would implement the dirty-flag debounced worker exactly as AI described. But production trade-offs should not be imported into a challenge system that runs on a laptop for 10 minutes.
