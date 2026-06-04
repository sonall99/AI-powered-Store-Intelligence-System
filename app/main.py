import time
import uuid
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError, SQLAlchemyError
import os

# Apne baaki imports
from database import init_db
from sessions import load_pos_data
from ingestion import ingest_events
from metrics import get_metrics
from funnel import get_funnel
from heatmap import get_heatmap
from anomalies import get_anomalies
from health import get_health

logger = structlog.get_logger()
app = FastAPI(title="Store Intelligence API — ST1008")

DASHBOARD_PATH = "/dashboard" if os.path.exists("/dashboard") else "dashboard"

app.mount("/static", StaticFiles(directory=DASHBOARD_PATH), name="static")

# =====================================================================
# 1. MIDDLEWARE: STRUCTURED LOGGING & TRACE ID (Part C Requirement)
# =====================================================================
@app.middleware("http")
async def add_structured_logging_and_trace(request: Request, call_next):
    start_time = time.time()
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id  # Attach trace_id to request
    
    response = await call_next(request)
    
    latency_ms = round((time.time() - start_time) * 1000, 2)
    
    # Logs trace_id, endpoint, latency_ms, status_code format me
    logger.info("api_request",
        trace_id=trace_id,
        endpoint=request.url.path,
        latency_ms=latency_ms,
        status_code=response.status_code
    )
    return response

# =====================================================================
# 2. GRACEFUL DEGRADATION: DATABASE 503 (Part C Requirement)
# =====================================================================
@app.exception_handler(OperationalError)
@app.exception_handler(SQLAlchemyError)
async def database_unavailable_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.error("database_unavailable", trace_id=trace_id, error=str(type(exc).__name__))
    
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable", 
            "reason": "database_unavailable",
            "retry_after": 30,
            "message": "Database is currently unavailable."
        }
    )

# =====================================================================
# 3. GRACEFUL DEGRADATION: UNHANDLED 500 (No Raw Stack Traces)
# =====================================================================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.error("unhandled_exception", trace_id=trace_id, error=str(type(exc).__name__))
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error", 
            "trace_id": trace_id, 
            "message": "An internal error occurred. Check logs with trace_id."
        }
    )

# =====================================================================
# APP ROUTES & EVENTS
# =====================================================================
@app.on_event("startup")
def startup():
    init_db()
    load_pos_data()
    logger.info("api_started")

@app.get("/dashboard")
def dashboard():
    return FileResponse(f"{DASHBOARD_PATH}/index.html")

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