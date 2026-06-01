import os
import pandas as pd
from datetime import timedelta
import structlog

logger = structlog.get_logger()

# Module-level POS cache
_POS_DATA = []

def load_pos_data():
    global _POS_DATA
    pos_path = os.getenv("POS_CSV_PATH", "data/pos_transactions.csv")
    if not os.path.exists(pos_path):
        logger.warning("pos_file_not_found", path=pos_path)
        return

    df = pd.read_csv(pos_path)
    loaded = []
    for _, row in df.iterrows():
        try:
            dt_str = f"{row['order_date']} {row['order_time']}"
            dt = pd.to_datetime(dt_str, format="%d-%m-%Y %H:%M:%S")
            dt_utc = dt - timedelta(hours=5, minutes=30)
            loaded.append({
                "invoice": row["invoice_number"],
                "store_id": row["store_id"],
                "timestamp_ms": int(dt_utc.timestamp() * 1000),
                "amount": float(row.get("total_amount", 0))
            })
        except Exception as e:
            continue

    _POS_DATA = loaded
    logger.info("pos_loaded", count=len(_POS_DATA))

def get_pos_data() -> list:
    return _POS_DATA