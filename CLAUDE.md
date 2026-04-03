# Stream Attention — Multi-OTT Attention-Aware Player

## Project Overview
Multi-OTT attention-aware streaming player with drowsiness detection, emotion-aware playback, attention analytics, smart audio fade, and multi-person weighted attention. Supports Netflix, Prime Video, YouTube, Disney+ Hotstar, JioCinema, and Apple TV+.

**Core concept:** webcam + MediaPipe 478-point face mesh → auto-pause when you look away → seek back exactly how long you were absent.

---

## Project Structure

```
Attention-Aware-Netflix-Player-Enhanced/
├── head/                          # All Python source code
│   ├── stream_attention.py       # Main app: single viewer, all HIGH/MED features
│   ├── stream_attention_2p.py    # Two-viewer mode (original 2P logic)
│   ├── stream_attention_4p.py    # Four-viewer weighted group mode
│   ├── analytics.py               # Session event logger → head/sessions/*.json
│   ├── platforms.py               # Multi-OTT platform config dict
│   └── face_landmarker.task       # MediaPipe model (NOT in git, download separately)
├── dashboard.html                 # Interactive analytics UI (Chart.js)
├── serve_dashboard.py             # HTTP server that injects session data into dashboard
├── netflix_seek_test.py           # CLI playback control tool (stdlib only)
├── sessions/                      # Root sessions dir (.gitkeep only)
├── requirements.txt
├── README.md
└── CLAUDE.md                      # This file
```

---

## How to Run

### Prerequisites
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Download MediaPipe model (not in git):
# Place face_landmarker.task in head/ directory
# Get from: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

**macOS:** System Settings → Privacy & Security → Camera + Automation → allow Terminal to control Chrome.  
**Windows:** `start chrome.exe --remote-debugging-port=9222` before running.

### Single Viewer
```bash
python head/stream_attention.py
python head/stream_attention.py --platform prime
python head/stream_attention.py --dashboard        # opens analytics on quit
python head/stream_attention.py --camera-url http://192.168.1.100:8080/video  # WiFi phone camera
python head/stream_attention.py --smart-seek       # only rewind if absent < 30s
python head/stream_attention.py --study-mode       # strict thresholds + Pomodoro log
python head/stream_attention.py --parental         # pause when face leaves frame
```

### Multi-Person
```bash
python head/stream_attention_2p.py                 # 2 viewers, both must look
python head/stream_attention_4p.py                 # up to 4 viewers, weighted
python head/stream_attention_4p.py --threshold 0.6 # custom threshold
```

### Analytics Dashboard
```bash
python serve_dashboard.py    # serves dashboard.html with real session data injected
```
> ⚠️ Do NOT just open `dashboard.html` directly — it needs session data injected via `serve_dashboard.py` to show real data. Opening via file:// shows only demo data.

### CLI Playback Control
```bash
python netflix_seek_test.py --play
python netflix_seek_test.py --pause
python netflix_seek_test.py --get-time
python netflix_seek_test.py --minutes 18 --seconds 30
```

---

## Implemented Features

| Feature | Priority | File | Status |
|---------|----------|------|--------|
| Head pose tracking (yaw/pitch) | Base | stream_attention.py | ✅ Done |
| Drowsiness detection (EAR) | HIGH | stream_attention.py | ✅ Done |
| Attention analytics + session JSON | HIGH | analytics.py + dashboard.html | ✅ Done |
| Multi-OTT support (6 platforms) | HIGH | platforms.py + stream_attention.py | ✅ Done |
| Emotion-aware playback | MED | stream_attention.py | ✅ Done |
| Smart audio fade | MED | stream_attention.py | ✅ Done |
| Multi-person weighted attention (4P) | MED | stream_attention_4p.py | ✅ Done |
| 2-person mode | MED | stream_attention_2p.py | ✅ Done |
| Mobile Camera Companion (WiFi) | MED | stream_attention.py --camera-url | ✅ Done |
| Smart Seek-Back (dialogue-aware) | LOW | stream_attention.py --smart-seek | ✅ Done |
| Study Mode (Pomodoro) | LOW | stream_attention.py --study-mode | ✅ Done |
| Parental Attention Guard | LOW | stream_attention.py --parental | ✅ Done |

---

## Known Gaps / Issues

### ~~1. `playsound` missing from requirements.txt~~ FIXED
Added to requirements.txt.

### 1. (Previously issue #2) Dashboard requires serve_dashboard.py
The spec requires `pip install playsound scipy`. Currently `playsound` is absent.
The drowsiness alert uses macOS `afplay` only — no cross-platform audio.
**Fix:** Add `playsound>=1.3.0` to requirements.txt AND update `play_alert()` in `stream_attention.py` to use playsound on non-macOS.

### 2. Sessions directory path mismatch
`analytics.py` saves to `head/sessions/` (relative to its own location).
`sessions/.gitkeep` is in the project root but is never written to.
`serve_dashboard.py` reads from `head/sessions/` (correct).
**Status:** Functionally OK but the root `sessions/` dir is misleading. Consider removing `sessions/.gitkeep` from root.

### 3. `stream_attention_4p.py` doesn't use platforms.py
Hardcodes `_URL_FILTER = "netflix.com"` and doesn't support `--platform` flag.
4P mode only works with Netflix. Should import from `platforms.py`.

### 4. `stream_attention_2p.py` likely same platform limitation
Needs verification — likely also hardcodes Netflix.

### 5. Mobile Camera Companion (MED priority) — not implemented
Would use phone camera over WiFi instead of webcam. Requires network stream receiver.

### 6. LOW priority features not built
Smart Seek-Back, Study Mode, Parental Attention Guard — all LOW priority, can be added later.

---

## Architecture Notes

### JS Injection Strategy
- **macOS:** AppleScript → `execute tab javascript` in Google Chrome
- **Windows:** Chrome DevTools Protocol (CDP) via WebSocket on port 9222
- Both paths unified in `_nf_js()` helper that wraps JS with Netflix API + HTML5 video fallback

### State Machine (single viewer)
```
WATCHING  — face visible, head forward
AWAY      — head turned (pauses after AWAY_GRACE_SEC=1.5s)
ABSENT    — face left frame (accumulates seek-back time)
DROWSY    — EAR < 0.25 for 2s while facing screen (pauses + alert)
```

### EAR Formula
```
EAR = (||p2-p6|| + ||p3-p5||) / (2 × ||p1-p4||)
Left eye landmarks:  [362, 385, 387, 263, 373, 380]
Right eye landmarks: [33, 160, 158, 133, 153, 144]
Threshold: 0.25, Grace: 2.0 seconds
```

### Emotion → Playback Actions
| Emotion | Signal | Action |
|---------|--------|--------|
| Confused | browInnerUp > 0.6 | Rewind 15s |
| Surprised | eyeWideLeft/Right > 0.7 | Bookmark |
| Bored | 3+ away events in 2min | Skip +30s |

### Multi-Person (4P) Scoring
```
group_score = sum(per_person_looking) / total_detected_faces
Pause if group_score < ATTENTION_THRESHOLD (default 0.5)
```

### Session Data Format
```json
{
  "date": "2025-01-15", "time": "20:30",
  "platform": "netflix", "title": "...",
  "duration_sec": 3600, "attention_pct": 87,
  "events": [
    {"t": 1234567, "type": "away", "video_ms": 45230},
    {"t": 1234569, "type": "back", "video_ms": 47100}
  ]
}
```

---

## Supported Platforms
| Platform | URL Filter | Method |
|----------|------------|--------|
| Netflix | netflix.com | Netflix JS API + HTML5 fallback |
| Prime Video | primevideo.com | HTML5 video element |
| JioCinema | jiocinema.com | HTML5 video element |
| Disney+ Hotstar | hotstar.com | HTML5 video element |
| YouTube | youtube.com | movie_player API |
| Apple TV+ | tv.apple.com | HTML5 video element |

---

## Tech Stack
- **Google MediaPipe** — Face landmark detection (478 points, face blendshapes)
- **OpenCV (cv2)** — Webcam feed, frame processing, HUD overlay
- **Python 3.10+** — Core scripting
- **Chrome DevTools Protocol** — JS injection on Windows
- **AppleScript** — JS injection on macOS
- **Chart.js (CDN)** — Analytics dashboard
- **playsound / afplay** — Drowsiness alert audio
- **scipy** — EAR signal smoothing

## Hard Limits
- Google Chrome only (no Firefox/Safari)
- Mac or Windows PC only (no Smart TV/Fire Stick)
- Webcam required (built-in or USB)
- Netflix mobile app not supported (sandboxed)

---

## Built
Stream Attention — MIT License | Built 2025-2026
