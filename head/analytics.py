"""
analytics.py — Session Event Logger & Data Persistence
=======================================================
Collects real-time viewing events and writes them to JSON files
in the sessions/ directory for later analysis via dashboard.html.

Usage (from stream_attention.py):
    from analytics import SessionLogger
    logger = SessionLogger(platform="netflix")
    logger.log("away", video_ms=45230)
    logger.log("back", video_ms=47100)
    path = logger.save(title="Stranger Things S4E1", duration_sec=3600, attention_pct=87)

Event types logged:
    looking     — user is looking at the screen
    away        — user looked away (head turned)
    absent      — user left the camera frame
    back        — user returned to frame
    drowsy      — drowsiness pause triggered
    confused_rewind  — confusion-triggered rewind
    surprised_bookmark — surprise moment bookmarked
    bored_skip  — boredom-triggered skip
"""

import json
import os
import time
from datetime import datetime


SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")


class SessionLogger:
    """Thread-safe session event logger."""

    def __init__(self, platform: str = "netflix"):
        self.platform   = platform
        self.start_time = time.time()
        self.events: list[dict] = []
        os.makedirs(SESSIONS_DIR, exist_ok=True)

    def log(self, event_type: str, video_ms: float = -1):
        """Append a timestamped event entry."""
        self.events.append({
            "t":        int(time.time() * 1000),   # wall-clock ms
            "type":     event_type,
            "video_ms": int(video_ms) if video_ms >= 0 else -1,
        })

    def save(self, title: str = "Unknown", duration_sec: int = 0,
             attention_pct: int = 0) -> str:
        """Serialise all events to a dated JSON file and return its path."""
        now       = datetime.now()
        filename  = now.strftime("%Y-%m-%d_%H-%M") + ".json"
        filepath  = os.path.join(SESSIONS_DIR, filename)

        payload = {
            "date":          now.strftime("%Y-%m-%d"),
            "time":          now.strftime("%H:%M"),
            "platform":      self.platform,
            "title":         title,
            "duration_sec":  duration_sec,
            "attention_pct": attention_pct,
            "events":        self.events,
        }

        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

        return filepath
