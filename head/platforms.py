"""
platforms.py — Multi-OTT Platform Configuration
=================================================
Defines URL filters and JavaScript methods for each supported streaming platform.
Auto-detection scans all Chrome tabs and returns the first matched platform.

Supported:
  netflix     → Netflix JS API + video element fallback
  prime       → Amazon Prime Video (HTML5 video)
  youtube     → YouTube (movie_player API)
  hotstar     → Disney+ Hotstar (HTML5 video)
  jiocinema   → JioCinema (HTML5 video)
  appletv     → Apple TV+ (HTML5 video)
"""

PLATFORMS: dict[str, dict] = {
    "netflix": {
        "url_filter": "netflix.com",
        "name":       "Netflix",
        "color":      (0, 0, 180),       # BGR red
    },
    "prime": {
        "url_filter": "primevideo.com",
        "name":       "Prime Video",
        "color":      (180, 120, 0),     # BGR blue-ish
    },
    "youtube": {
        "url_filter": "youtube.com",
        "name":       "YouTube",
        "color":      (0, 0, 200),       # BGR red
    },
    "hotstar": {
        "url_filter": "hotstar.com",
        "name":       "Disney+ Hotstar",
        "color":      (200, 100, 0),     # BGR blue
    },
    "jiocinema": {
        "url_filter": "jiocinema.com",
        "name":       "JioCinema",
        "color":      (100, 0, 200),     # BGR purple
    },
    "appletv": {
        "url_filter": "tv.apple.com",
        "name":       "Apple TV+",
        "color":      (200, 200, 200),   # BGR white-ish
    },
}
