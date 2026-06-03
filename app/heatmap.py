from sqlmodel import Session, select
from models import EventRecord
from database import engine

def get_heatmap(store_id: str) -> dict:
    with Session(engine) as db:
        events = db.exec(
            select(EventRecord).where(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False
            )
        ).all()

    zone_stats = {}
    for e in events:
        if not e.zone_id:
            continue
        if e.zone_id not in zone_stats:
            zone_stats[e.zone_id] = {
                "visits": 0,
                "dwell_total": 0,
                "sessions": set()
            }
        zone_stats[e.zone_id]["visits"] += 1
        zone_stats[e.zone_id]["dwell_total"] += e.dwell_ms
        zone_stats[e.zone_id]["sessions"].add(e.session_id)

    if not zone_stats:
        return {"store_id": store_id, "zones": []}

    max_score = max(
        s["visits"] * (s["dwell_total"] / max(s["visits"], 1))
        for s in zone_stats.values()
    )

    zones = []
    for zone_id, stats in zone_stats.items():
        avg_dwell = stats["dwell_total"] / max(stats["visits"], 1)
        score = stats["visits"] * avg_dwell
        heat = round((score / max_score) * 100) if max_score > 0 else 0
        zones.append({
            "zone_id": zone_id,
            "visit_frequency": stats["visits"],
            "avg_dwell_ms": round(avg_dwell),
            "heat_score": heat,
            "data_confidence": "LOW" if len(stats["sessions"]) < 5 else "OK"
        })

    return {
        "store_id": store_id,
        "zones": sorted(zones, key=lambda x: -x["heat_score"])
    }