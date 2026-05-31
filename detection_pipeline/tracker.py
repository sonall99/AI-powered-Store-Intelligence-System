# detection_pipeline/tracker.py
"""
Manages visitor identity across frames.
- Assigns visitor_id to each ByteTrack track
- Tracks zone entry/exit via polygon containment
- Detects re-entry via appearance buffer
- Detects staff via HSV uniform color
- Manages dwell timing for ZONE_DWELL events
"""

import uuid
import cv2
import numpy as np
from datetime import datetime, timezone
from typing import Optional
from detection_pipeline.emit import make_event, StoreEvent


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
    
    def __init__(self, visitor_id: str, session_id: str, is_staff: bool, timestamp_ms: int):
        self.visitor_id = visitor_id
        self.session_id = session_id
        self.is_staff = is_staff
        self.created_at_ms = timestamp_ms
        self.last_seen_ms = timestamp_ms
        self.current_zone: Optional[str] = None
        self.zone_entered_at_ms: Optional[int] = None
        self.last_dwell_emit_ms: Optional[int] = None
        self.session_seq = 0
        self.appearance: Optional[np.ndarray] = None
        self.crossed_tripwire = False


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
        
        # Staff uniform HSV
        staff_cfg = store_layout.get("staff_uniform", {})
        self.staff_hsv_lower = staff_cfg.get("hsv_lower", [0, 0, 0])
        self.staff_hsv_upper = staff_cfg.get("hsv_upper", [180, 255, 80])
        
        # Active tracks: ByteTrack int ID → TrackState
        self.active_tracks: dict[int, TrackState] = {}
        
        # Re-ID buffer: visitor_id → {embedding, exited_at_ms}
        # TTL = 30 minutes
        self.reid_buffer: dict[str, dict] = {}
        self.reentry_window_ms = store_layout.get("reentry_window_minutes", 30) * 60 * 1000
        
        # Previous centroids for tripwire crossing detection
        self.prev_centroids: dict[int, tuple] = {}
        
        # Billing zone queue tracking
        self.billing_zones = {z["zone_id"] for z in store_layout["zones"]
                              if z.get("is_billing")}
        self.current_queue_depth = 0
        
        self.clip_id = f"{self.store_id}_{self.camera_id}"
    
    def process_frame(self, result, frame_idx: int, timestamp_ms: int) -> list[StoreEvent]:
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
                
                # Determine if staff
                if self.is_stockroom:
                    # Everything in stockroom is staff
                    is_staff = True
                    staff_conf = 1.0
                else:
                    is_staff, staff_conf = is_staff_by_uniform(
                        frame, xyxy,
                        self.staff_hsv_lower,
                        self.staff_hsv_upper
                    )
                
                # Compute appearance embedding
                appearance = compute_appearance(frame, xyxy)
                
                # New track
                if track_id not in self.active_tracks:
                    visitor_id, session_id, is_reentry = self._assign_identity(
                        appearance, timestamp_ms, is_staff
                    )
                    state = TrackState(visitor_id, session_id, is_staff, timestamp_ms)
                    state.appearance = appearance
                    self.active_tracks[track_id] = state
                    
                    # Emit ENTRY (only from entry camera, only for customers)
                    if (self.has_tripwire and not is_staff
                            and not self.is_stockroom):
                        # Don't emit yet — wait for tripwire crossing
                        pass
                    elif (not self.has_tripwire and not is_staff
                          and not self.is_stockroom):
                        # Non-entry cameras: emit entry when first seen
                        if is_reentry:
                            events.append(make_event(
                                store_id=self.store_id,
                                camera_id=self.camera_id,
                                visitor_id=visitor_id,
                                session_id=session_id,
                                event_type="REENTRY",
                                timestamp_ms=timestamp_ms,
                                is_staff=False,
                                confidence=conf,
                                frame_number=frame_idx,
                                clip_id=self.clip_id,
                            ))
                
                state = self.active_tracks[track_id]
                state.last_seen_ms = timestamp_ms
                if appearance is not None:
                    state.appearance = appearance
                
                # Tripwire crossing (entry camera only)
                if self.has_tripwire and track_id in self.prev_centroids:
                    crossing = self._check_tripwire_crossing(
                        self.prev_centroids[track_id], centroid
                    )
                    if crossing and not state.crossed_tripwire:
                        state.crossed_tripwire = True
                        state.session_seq += 1
                        event_type = "ENTRY" if crossing == "entry" else "EXIT"
                        
                        if event_type == "EXIT":
                            self._add_to_reid_buffer(state)
                        
                        if not state.is_staff:
                            events.append(make_event(
                                store_id=self.store_id,
                                camera_id=self.camera_id,
                                visitor_id=state.visitor_id,
                                session_id=state.session_id,
                                event_type=event_type,
                                timestamp_ms=timestamp_ms,
                                is_staff=False,
                                confidence=conf,
                                frame_number=frame_idx,
                                clip_id=self.clip_id,
                                session_seq=state.session_seq,
                            ))
                        state.crossed_tripwire = False
                
                # Zone detection (non-entry cameras)
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
            self._add_to_reid_buffer(state)
            
            # Emit EXIT for zone cameras when track is lost
            if (not self.has_tripwire and not self.is_stockroom
                    and not state.is_staff and state.current_zone):
                dwell = timestamp_ms - (state.zone_entered_at_ms or timestamp_ms)
                events.append(make_event(
                    store_id=self.store_id,
                    camera_id=self.camera_id,
                    visitor_id=state.visitor_id,
                    session_id=state.session_id,
                    event_type="ZONE_EXIT",
                    timestamp_ms=timestamp_ms,
                    zone_id=state.current_zone,
                    dwell_ms=dwell,
                    is_staff=False,
                    confidence=0.5,
                    frame_number=frame_idx,
                    clip_id=self.clip_id,
                ))
        
        # Update billing queue depth
        billing_visitors = sum(
            1 for s in self.active_tracks.values()
            if s.current_zone in self.billing_zones and not s.is_staff
        )
        self.current_queue_depth = billing_visitors
        
        return events
    
    def _assign_identity(self, appearance, timestamp_ms, is_staff):
        """
        Check Re-ID buffer. Return (visitor_id, session_id, is_reentry).
        """
        if appearance is not None:
            for vid, data in self.reid_buffer.items():
                # Check TTL
                age_ms = timestamp_ms - data["exited_at_ms"]
                if age_ms > self.reentry_window_ms:
                    continue
                # Check similarity
                if data["embedding"] is not None:
                    sim = cosine_similarity(appearance, data["embedding"])
                    if sim > 0.75:
                        # Re-entry detected
                        new_session_id = f"SES_{uuid.uuid4().hex[:8]}"
                        return vid, new_session_id, True
        
        # New visitor
        visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
        session_id = f"SES_{uuid.uuid4().hex[:8]}"
        return visitor_id, session_id, False
    
    def _add_to_reid_buffer(self, state: TrackState):
        self.reid_buffer[state.visitor_id] = {
            "embedding": state.appearance,
            "exited_at_ms": state.last_seen_ms,
        }
    
    def _check_tripwire_crossing(self, prev: tuple, curr: tuple) -> Optional[str]:
        """
        Returns 'entry', 'exit', or None.
        CAM 3: moving from high Y (outside) to low Y (inside) = ENTRY.
        """
        if not self.tripwire:
            return None
        
        wire_y = self.tripwire.get("y1", 620)
        inside_is = self.tripwire.get("inside_is", "top")
        
        if inside_is == "top":
            # top = inside (lower Y value)
            if prev[1] > wire_y and curr[1] <= wire_y:
                return "entry"
            if prev[1] <= wire_y and curr[1] > wire_y:
                return "exit"
        
        return None
    
    def _process_zone(self, state: TrackState, centroid: tuple,
                      timestamp_ms: int, conf: float, frame_idx: int) -> list:
        events = []
        
        # Find which zone the centroid is in
        current_zone = None
        for zone_id, polygon in self.zone_polygons.items():
            if point_in_polygon(centroid, polygon):
                current_zone = zone_id
                break
        
        # Zone transition
        if current_zone != state.current_zone:
            
            # Zone exit
            if state.current_zone is not None:
                dwell = timestamp_ms - (state.zone_entered_at_ms or timestamp_ms)
                events.append(make_event(
                    store_id=self.store_id,
                    camera_id=self.camera_id,
                    visitor_id=state.visitor_id,
                    session_id=state.session_id,
                    event_type="ZONE_EXIT",
                    timestamp_ms=timestamp_ms,
                    zone_id=state.current_zone,
                    dwell_ms=dwell,
                    is_staff=state.is_staff,
                    confidence=conf,
                    frame_number=frame_idx,
                    clip_id=self.clip_id,
                ))
            
            # Zone enter
            if current_zone is not None:
                state.session_seq += 1
                
                # Billing queue join
                if current_zone in self.billing_zones:
                    queue_at_entry = sum(
                        1 for s in self.active_tracks.values()
                        if s.current_zone in self.billing_zones and not s.is_staff
                    )
                    event_type = ("BILLING_QUEUE_JOIN"
                                  if queue_at_entry > 0 else "ZONE_ENTER")
                    events.append(make_event(
                        store_id=self.store_id,
                        camera_id=self.camera_id,
                        visitor_id=state.visitor_id,
                        session_id=state.session_id,
                        event_type=event_type,
                        timestamp_ms=timestamp_ms,
                        zone_id=current_zone,
                        is_staff=state.is_staff,
                        confidence=conf,
                        frame_number=frame_idx,
                        clip_id=self.clip_id,
                        session_seq=state.session_seq,
                        queue_depth=queue_at_entry,
                    ))
                else:
                    events.append(make_event(
                        store_id=self.store_id,
                        camera_id=self.camera_id,
                        visitor_id=state.visitor_id,
                        session_id=state.session_id,
                        event_type="ZONE_ENTER",
                        timestamp_ms=timestamp_ms,
                        zone_id=current_zone,
                        is_staff=state.is_staff,
                        confidence=conf,
                        frame_number=frame_idx,
                        clip_id=self.clip_id,
                        session_seq=state.session_seq,
                    ))
                
                state.zone_entered_at_ms = timestamp_ms
                state.last_dwell_emit_ms = timestamp_ms
            
            state.current_zone = current_zone
        
        # ZONE_DWELL — emit every 30 seconds of continuous dwell
        elif (current_zone is not None and state.last_dwell_emit_ms is not None):
            dwell_since_last = timestamp_ms - state.last_dwell_emit_ms
            if dwell_since_last >= 30_000:
                total_dwell = timestamp_ms - (state.zone_entered_at_ms or timestamp_ms)
                events.append(make_event(
                    store_id=self.store_id,
                    camera_id=self.camera_id,
                    visitor_id=state.visitor_id,
                    session_id=state.session_id,
                    event_type="ZONE_DWELL",
                    timestamp_ms=timestamp_ms,
                    zone_id=current_zone,
                    dwell_ms=total_dwell,
                    is_staff=state.is_staff,
                    confidence=conf,
                    frame_number=frame_idx,
                    clip_id=self.clip_id,
                ))
                state.last_dwell_emit_ms = timestamp_ms
        
        return events