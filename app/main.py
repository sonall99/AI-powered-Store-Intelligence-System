from fastapi import FastAPI
import structlog

from app.database import init_db
from app.sessions import load_pos_data
from app.ingestion import ingest_events
from app.metrics import get_metrics
from app.funnel import get_funnel
from app.heatmap import get_heatmap
from app.anomalies import get_anomalies
from app.health import get_health

logger = structlog.get_logger()
app = FastAPI(title="Store Intelligence API — ST1008")

@app.on_event("startup")
def startup():
    init_db()
    load_pos_data()
    logger.info("api_started")

@app.get("/health")
def health():
    return get_health()

@app.post("/events/ingest")
def ingest(payload: dict):
    return ingest_events(payload)

@app.get("/stores/{store_id}/metrics")
def metrics(store_id: str):
    return get_metrics(store_id)

@app.get("/stores/{store_id}/funnel")
def funnel(store_id: str):
    return get_funnel(store_id)

@app.get("/stores/{store_id}/heatmap")
def heatmap(store_id: str):
    return get_heatmap(store_id)

@app.get("/stores/{store_id}/anomalies")
def anomalies(store_id: str):
    return get_anomalies(store_id)