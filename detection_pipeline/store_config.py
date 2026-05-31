import json
import os


def load_store_layout(config_path: str = None) -> dict:
    """
    Load store layout from JSON.
    Path resolution order:
    1. Explicit argument
    2. STORE_LAYOUT_PATH environment variable
    3. Default: config/store_layout.json
    """
    if config_path is None:
        config_path = os.getenv("STORE_LAYOUT_PATH", "config/store_layout.json")

    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"\nStore layout not found at: {config_path}\n"
            f"Make sure config/store_layout.json exists.\n"
            f"Or set environment variable: STORE_LAYOUT_PATH=/path/to/layout.json"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        layout = json.load(f)

    _validate_layout(layout)
    return layout


def _validate_layout(layout: dict):
    """Fail fast if required fields are missing."""
    required_top = ["store_id", "store_name", "cameras", "zones"]
    for field in required_top:
        if field not in layout:
            raise ValueError(f"store_layout.json missing required field: '{field}'")

    for cam in layout["cameras"]:
        for field in ["camera_id", "filename", "role"]:
            if field not in cam:
                raise ValueError(
                    f"Camera entry missing required field: '{field}' in {cam}"
                )


def get_camera_config(layout: dict, filename: str) -> dict:
    """Get camera config by MP4 filename."""
    for cam in layout["cameras"]:
        if cam["filename"] == filename:
            return cam
    available = [c["filename"] for c in layout["cameras"]]
    raise ValueError(
        f"No camera config found for '{filename}'.\n"
        f"Available filenames in layout: {available}"
    )


def get_billing_zones(layout: dict) -> set:
    """Return set of zone_ids where is_billing=True."""
    return {z["zone_id"] for z in layout["zones"] if z.get("is_billing")}


def get_staff_only_zones(layout: dict) -> set:
    """Return set of zone_ids that are staff-only (e.g. stockroom)."""
    return {z["zone_id"] for z in layout["zones"] if z.get("is_staff_only")}


def get_zone_ids(layout: dict) -> set:
    """Return all zone_ids defined in the layout."""
    return {z["zone_id"] for z in layout["zones"]}


def get_cameras_by_role(layout: dict, role: str) -> list:
    """Get all cameras with a specific role."""
    return [c for c in layout["cameras"] if c.get("role") == role]


def get_entry_camera(layout: dict) -> dict | None:
    """Return the entry/exit camera config, or None if not defined."""
    cams = get_cameras_by_role(layout, "entry_exit")
    return cams[0] if cams else None


def get_billing_camera(layout: dict) -> dict | None:
    """Return the billing camera config, or None if not defined."""
    cams = get_cameras_by_role(layout, "billing")
    return cams[0] if cams else None


def is_stockroom_camera(camera_config: dict) -> bool:
    """Check if a camera covers stockroom only."""
    return camera_config.get("is_stockroom", False)


def get_store_id(layout: dict) -> str:
    return layout["store_id"]


def get_staff_hsv(layout: dict) -> tuple:
    """
    Returns (hsv_lower, hsv_upper) as lists.
    Defaults to black uniform if not configured.
    """
    staff_cfg = layout.get("staff_uniform", {})
    lower = staff_cfg.get("hsv_lower", [0, 0, 0])
    upper = staff_cfg.get("hsv_upper", [180, 255, 80])
    return lower, upper


def get_reentry_window_ms(layout: dict) -> int:
    minutes = layout.get("reentry_window_minutes", 30)
    return minutes * 60 * 1000


def get_pos_correlation_window_ms(layout: dict) -> int:
    minutes = layout.get("pos_correlation_window_minutes", 5)
    return minutes * 60 * 1000