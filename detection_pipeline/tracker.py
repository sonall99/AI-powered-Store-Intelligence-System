# detection_pipeline/tracker.py
"""
Manages visitor identity across frames.
- Assigns visitor_id to each ByteTrack track
- Tracks zone entry/exit via polygon containment
- Detects re-entry via appearance buffer
- Detects staff via HSV uniform color (with dynamic thresholds and geofencing)
- Manages dwell timing for ZONE_DWELL events
"""

import uuid
import cv2
import numpy as np
from datetime import datetime, timezone
from typing import Optional
from emit import make_event, StoreEvent
import collections


def point_in_polygon(point: tuple, polygon: list) -> bool:
    """Check if (x,y) centroid is inside a polygon."""
    if not polygon:
        return False
    pts = np.array(polygon, dtype=np.int32)
    result = cv2.pointPolygonTest(pts, point, False)
    return result >= 0


def get_centroid(xyxy) -> tuple:
    """Get bottom-center of bounding box (feet position — more accurate for zone detection)."""
    x1, y1, x2, y2 = map(float, xyxy)
    cx = (x1 + x2) / 2
    cy = y2  # bottom of box = floor position
    return (cx, cy)


def compute_appearance(frame, xyxy) -> Optional[np.ndarray]:
    """
    HSV color histogram over torso region.
    Returns 192-dim descriptor or None if crop too small.
    """
    x1, y1, x2, y2 = map(int, xyxy)
    h = y2 - y1
    w = x2 - x1
    if h < 40 or w < 20:
        return None
    
    # Torso = middle 50% of bounding box height
    torso_y1 = y1 + h // 4
    torso_y2 = y1 + 3 * h // 4
    torso = frame[torso_y1:torso_y2, x1:x2]
    
    if torso.size == 0:
        return None
    
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    # 8 vertical strips × 24 bins = 192 dims
    strips = np.array_split(hsv, 8, axis=1)
    hist = []
    for strip in strips:
        h_hist = cv2.calcHist([strip], [0], None, [24], [0, 180])
        hist.extend(h_hist.flatten())
    
    descriptor = np.array(hist, dtype=np.float32)
    norm = np.linalg.norm(descriptor)
    if norm > 0:
        descriptor /= norm
    return descriptor


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def is_staff_by_uniform(frame, xyxy, hsv_lower, hsv_upper, threshold=0.40) -> tuple:
    """
    Returns (is_staff: bool, confidence: float)
    Black uniform detection: low Value in HSV.
    """
    x1, y1, x2, y2 = map(int, xyxy)
    h = y2 - y1
    if h < 30:
        return False, 0.0
    
    torso_y1 = y1 + h // 4
    torso_y2 = y1 + 3 * h // 4
    torso = frame[torso_y1:torso_y2, x1:x2]
    
    if torso.size == 0:
        return False, 0.0
    
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    lower = np.array(hsv_lower)
    upper = np.array(hsv_upper)
    mask = cv2.inRange(hsv, lower, upper)
    ratio = np.count_nonzero(mask) / mask.size
    
    return ratio >= threshold, round(float(ratio), 3)


class TrackState:
    """State for a single tracked person across frames."""
    
    def __init__(self, visitor_id: str, is_staff: bool, timestamp_ms: int):
        self.visitor_id = visitor_id
        self.is_staff = is_staff
        self.created_at_ms = timestamp_ms
        self.last_seen_ms = timestamp_ms
        self.current_zone: Optional[str] = None
        self.zone_entered_at_ms: Optional[int] = None
        self.last_dwell_emit_ms: Optional[int] = None
        self.appearance: Optional[np.ndarray] = None
        self.crossed_tripwire = False
        
        # --- VARIABLES FOR DEBOUNCING & FLICKERING ---
        self.staff_history = collections.deque(maxlen=15)
        self.candidate_zone: Optional[str] = None
        self.candidate_frames: int = 0
        # REMOVED local_frag_buffer variables from here


class VisitorTracker:
    
    def __init__(self, store_layout: dict, camera_config: dict,
                 clip_start_time: datetime, fps: float):
        
        self.store_id = store_layout["store_id"]
        self.camera_config = camera_config
        self.camera_id = camera_config["camera_id"]
        self.clip_start_time = clip_start_time
        self.fps = fps
        self.is_stockroom = camera_config.get("is_stockroom", False)
        
        # Zone polygons for this camera
        self.zone_polygons = camera_config.get("zone_polygons", {})
        
        # Tripwire config
        self.has_tripwire = camera_config.get("has_tripwire", False)
        self.tripwire = camera_config.get("tripwire", {})
        
        # --- STAFF DETECTION CONFIG (Geofence & Thresholds) ---
        self.staff_zone_polygon = camera_config.get("staff_zone_polygon", None)
        staff_cfg = store_layout.get("staff_uniform", {})
        self.staff_hsv_lower = staff_cfg.get("hsv_lower", [0, 0, 0])
        self.staff_hsv_upper = staff_cfg.get("hsv_upper", [180, 255, 80])
        
        # Apply camera-specific override for staff threshold if it exists
        self.staff_threshold = camera_config.get(
            "staff_threshold_override",
            staff_cfg.get("pixel_ratio_threshold", 0.40)
        )
        
        # Active tracks: ByteTrack int ID → TrackState
        self.active_tracks: dict[int, TrackState] = {}
        
        # Re-ID buffer: visitor_id → {embedding, exited_at_ms}
        self.reid_buffer: dict[str, dict] = {}
        self.reentry_window_ms = store_layout.get("reentry_window_minutes", 30) * 60 * 1000

        # Local fragmentation buffer (Claude fix)
        self._local_frag_buffer = {}
        self._frag_window_ms = 2000
        
        # Previous centroids for tripwire crossing detection
        self.prev_centroids: dict[int, tuple] = {}
        
        # Billing zone queue tracking
        self.billing_zones = {z["zone_id"] for z in store_layout.get("zones", [])
                              if z.get("is_billing")}
        self.current_queue_depth = 0

    
    def _determine_staff(self, frame, xyxy, centroid: tuple) -> bool:
        """
        Cascading staff detection:
        1. Inside staff zone polygon → definitely staff
        2. Outside zone → check HSV uniform color (with dynamic threshold)
        3. Neither → customer
        """
        # Primary: spatial check
        if self.staff_zone_polygon:
            if point_in_polygon(centroid, self.staff_zone_polygon):
                return True
                
        # Secondary: HSV uniform color fallback
        is_staff, _ = is_staff_by_uniform(
            frame, xyxy,
            self.staff_hsv_lower,
            self.staff_hsv_upper,
            threshold=self.staff_threshold  # Using dynamic override
        )
        return is_staff
    
    def process_frame(self, result, frame_idx: int, timestamp_ms: int) -> list:
        """Process one frame of YOLO tracking results. Returns list of events."""
        events = []
        frame = result.orig_img
        
        # Track IDs seen this frame
        seen_track_ids = set()
        
        if result.boxes is not None and result.boxes.id is not None:
            for i, box in enumerate(result.boxes):
                if box.id is None:
                    continue
                
                track_id = int(box.id)
                conf = float(box.conf)
                xyxy = box.xyxy[0].cpu().numpy()
                centroid = get_centroid(xyxy)
                seen_track_ids.add(track_id)
               
                if track_id in self.active_tracks:
                    if self.active_tracks[track_id].crossed_tripwire:
                        self.active_tracks[track_id].crossed_tripwire = False
                
                # --- NEW CASCADING STAFF DETECTION ---
                if self.is_stockroom:
                    is_staff = True
                else:
                    is_staff = self._determine_staff(frame, xyxy, centroid)
                
                # Compute appearance embedding
                appearance = compute_appearance(frame, xyxy)
                
                # New track
                if track_id not in self.active_tracks:
                    visitor_id, is_reentry = self._assign_identity(
                        appearance, timestamp_ms, is_staff
                    )
                    state = TrackState(visitor_id, is_staff, timestamp_ms)
                    state.appearance = appearance
                    self.active_tracks[track_id] = state
                    
                    # Emit ENTRY
                    if (self.has_tripwire and not is_staff
                            and not self.is_stockroom):
                        pass
                    elif (not self.has_tripwire and not is_staff
                          and not self.is_stockroom):
                        if is_reentry:
                            events.append(make_event(
                                store_id=self.store_id,
                                camera_id=self.camera_id,
                                visitor_id=visitor_id,
                                event_type="REENTRY",
                                timestamp_ms=timestamp_ms,
                                is_staff=False,
                                confidence=conf
                            ))
                
                state = self.active_tracks[track_id]
                state.last_seen_ms = timestamp_ms
                if appearance is not None:
                    state.appearance = appearance

                # --- ROLLING AVERAGE FOR STAFF ---
                state.staff_history.append(is_staff)
                state.is_staff = sum(state.staff_history) > (len(state.staff_history) / 2)
                
                # Tripwire crossing
                if self.has_tripwire and track_id in self.prev_centroids:
                    crossing = self._check_tripwire_crossing(
                        self.prev_centroids[track_id], centroid
                    )
                    if crossing and not state.crossed_tripwire:
                        state.crossed_tripwire = True
                        event_type = "ENTRY" if crossing == "entry" else "EXIT"
                        
                        if event_type == "EXIT":
                            self._add_to_reid_buffer(state)
                        
                        if not state.is_staff:
                            events.append(make_event(
                                store_id=self.store_id,
                                camera_id=self.camera_id,
                                visitor_id=state.visitor_id,
                                event_type=event_type,
                                timestamp_ms=timestamp_ms,
                                is_staff=False,
                                confidence=conf
                            ))
                
                # Zone detection
                if not self.has_tripwire and not self.is_stockroom:
                    zone_events = self._process_zone(
                        state, centroid, timestamp_ms, conf, frame_idx
                    )
                    events.extend(zone_events)
                
                self.prev_centroids[track_id] = centroid
        
        # Handle lost tracks
        lost_ids = set(self.active_tracks.keys()) - seen_track_ids
        for track_id in lost_ids:
            state = self.active_tracks.pop(track_id)
            if self.has_tripwire:
                # Entry camera: add to global Re-ID buffer
                self._add_to_reid_buffer(state)

            else:
                # Zone camera: add to local fragmentation buffer only
                self._local_frag_buffer[state.visitor_id] = {
                    "embedding": state.appearance,
                    "lost_at_ms": timestamp_ms,
                }
            
            # Emit EXIT
            if (not self.has_tripwire and not self.is_stockroom
                    and not state.is_staff and state.current_zone):
                dwell = timestamp_ms - (state.zone_entered_at_ms or timestamp_ms)
                events.append(make_event(
                    store_id=self.store_id,
                    camera_id=self.camera_id,
                    visitor_id=state.visitor_id,
                    event_type="ZONE_EXIT",
                    timestamp_ms=timestamp_ms,
                    zone_id=state.current_zone,
                    dwell_ms=dwell,
                    is_staff=False,
                    confidence=0.5
                ))
        
        # Update billing queue depth
        billing_visitors = sum(
            1 for s in self.active_tracks.values()
            if s.current_zone in self.billing_zones and not s.is_staff
        )
        self.current_queue_depth = billing_visitors
        
        return events

    def _assign_identity(self, appearance, timestamp_ms, is_staff):
       
        # Zone cameras: purely local identity, no re-entry concept
        if not self.has_tripwire:

            # Check fragmentation buffer only (same camera, short gap)
            if appearance is not None:
                for vid, data in list(self._local_frag_buffer.items()):

                    age_ms = timestamp_ms - data["lost_at_ms"]

                    # Only check within 2-second fragmentation window
                    if age_ms > self._frag_window_ms:
                        del self._local_frag_buffer[vid]
                        continue

                    if data["embedding"] is not None:
                        sim = cosine_similarity(
                            appearance,
                            data["embedding"]
                        )

                        if sim > 0.65:
                            # Resume same session — occlusion, not re-entry
                            return vid, False

            # New person on this zone camera
            visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
            return visitor_id, False

        # Entry camera only: full Re-ID with re-entry detection
        if appearance is not None:

            for vid, data in list(self.reid_buffer.items()):

                age_ms = timestamp_ms - data["exited_at_ms"]

                # Expired — clean up
                if age_ms > self.reentry_window_ms:
                    del self.reid_buffer[vid]
                    continue

                if data["embedding"] is None:
                    continue

                sim = cosine_similarity(
                    appearance,
                    data["embedding"]
                )

                if sim > 0.75:

                    if age_ms < 60000:
                        # Track fragmentation at entry threshold
                        return vid, False

                    # Genuine re-entry
                    return vid, True

        # New visitor
        visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
        return visitor_id, False
    
    def _add_to_reid_buffer(self, state: TrackState):
        self.reid_buffer[state.visitor_id] = {
            "embedding": state.appearance,
            "exited_at_ms": state.last_seen_ms,
        }
    
    def _check_tripwire_crossing(self, prev: tuple, curr: tuple) -> Optional[str]:
        if not self.tripwire:
            return None
        
        wire_y = self.tripwire.get("y1", 620)
        wire_x1 = self.tripwire.get("x1", 450)
        wire_x2 = self.tripwire.get("x2", 950)
        inside_is = self.tripwire.get("inside_is", "top")
        
        x_in_bounds = (wire_x1 <= prev[0] <= wire_x2) or (wire_x1 <= curr[0] <= wire_x2)
        if not x_in_bounds:
            return None 
        
        buffer_size = 40 
        if inside_is == "top":
            if prev[1] > (wire_y + buffer_size) and curr[1] < (wire_y - buffer_size):
                return "entry"
            if prev[1] < (wire_y - buffer_size) and curr[1] > (wire_y + buffer_size):
                return "exit"
        elif inside_is == "bottom":
            if prev[1] < (wire_y - buffer_size) and curr[1] > (wire_y + buffer_size):
                return "entry"
            if prev[1] > (wire_y + buffer_size) and curr[1] < (wire_y - buffer_size):
                return "exit"
        
        return None
    
    def _process_zone(self, state: TrackState, centroid: tuple,
                      timestamp_ms: int, conf: float, frame_idx: int) -> list:
        events = []
        current_zone = None
        for zone_id, polygon in self.zone_polygons.items():
            if point_in_polygon(centroid, polygon):
                current_zone = zone_id
                break
        
        if current_zone != state.current_zone:
            if current_zone == state.candidate_zone:
                state.candidate_frames += 1
            else:
                state.candidate_zone = current_zone
                state.candidate_frames = 1
                
            if state.candidate_frames >= 15:
                if state.current_zone is not None:
                    dwell = timestamp_ms - (state.zone_entered_at_ms or timestamp_ms)
                    events.append(make_event(
                        store_id=self.store_id, camera_id=self.camera_id,
                        visitor_id=state.visitor_id,
                        event_type="ZONE_EXIT", timestamp_ms=timestamp_ms,
                        zone_id=state.current_zone, dwell_ms=dwell,
                        is_staff=state.is_staff, confidence=conf
                    ))
                
                if current_zone is not None:
                    if current_zone in self.billing_zones:
                        queue_at_entry = sum(
                            1 for s in self.active_tracks.values()
                            if s.current_zone in self.billing_zones and not s.is_staff
                        )
                        event_type = "BILLING_QUEUE_JOIN" if queue_at_entry > 0 else "ZONE_ENTER"
                        events.append(make_event(
                            store_id=self.store_id, camera_id=self.camera_id,
                            visitor_id=state.visitor_id,
                            event_type=event_type, timestamp_ms=timestamp_ms,
                            zone_id=current_zone, is_staff=state.is_staff,
                            confidence=conf
                        ))
                    else:
                        events.append(make_event(
                            store_id=self.store_id, camera_id=self.camera_id,
                            visitor_id=state.visitor_id,
                            event_type="ZONE_ENTER", timestamp_ms=timestamp_ms,
                            zone_id=current_zone, is_staff=state.is_staff,
                            confidence=conf
                        ))
                
                state.zone_entered_at_ms = timestamp_ms
                state.last_dwell_emit_ms = timestamp_ms
                state.current_zone = current_zone
                state.candidate_frames = 0
        else:
            state.candidate_frames = 0
            
        if (state.current_zone is not None and state.last_dwell_emit_ms is not None):
            dwell_since_last = timestamp_ms - state.last_dwell_emit_ms
            if dwell_since_last >= 30_000:
                total_dwell = timestamp_ms - (state.zone_entered_at_ms or timestamp_ms)
                events.append(make_event(
                    store_id=self.store_id, camera_id=self.camera_id,
                    visitor_id=state.visitor_id,
                    event_type="ZONE_DWELL", timestamp_ms=timestamp_ms,
                    zone_id=state.current_zone, dwell_ms=total_dwell,
                    is_staff=state.is_staff, confidence=conf
                ))
                state.last_dwell_emit_ms = timestamp_ms
        
        return events