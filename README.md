# 👁 Stream Attention — Multi-OTT Attention-Aware Player

> **Auto-pause any streaming platform when you look away, fall asleep, or leave the room.**  
> Supports Netflix, Prime Video, YouTube, Disney+ Hotstar, JioCinema, and Apple TV+.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![MediaPipe](https://img.shields.io/badge/MediaPipe-Face%20Landmarker-green) ![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-lightgrey) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Features

| Feature | Status | Description |
|---|---|---|
| **Multi-OTT Support** | ✅ | Netflix, Prime Video, YouTube, Hotstar, JioCinema, Apple TV+ |
| **Head Pose Tracking** | ✅ | Auto-pause when you look away, resume when you look back |
| **Seek-Back on Return** | ✅ | Rewinds exactly how long you were absent |
| **Drowsiness Detection** | ✅ | Eye Aspect Ratio (EAR) — pauses + alert if eyes close for 2s |
| **Attention Analytics** | ✅ | Session JSON logging + interactive Chart.js dashboard |
| **Emotion-Aware Playback** | ✅ | Auto-rewind on confusion, skip on boredom, bookmark on surprise |
| **Smart Audio Fade** | ✅ | Gradual volume fade before pause |
| **4-Person Group Mode** | ✅ | Weighted group attention with configurable threshold |
| **2-Person Mode** | ✅ | Both viewers must be watching |
| **WiFi Camera Support** | ✅ | Use your phone as a wireless webcam |
| **Smart Seek-Back** | ✅ | Skip rewind for long breaks (likely intentional) |
| **Study Mode** | ✅ | Stricter thresholds + Pomodoro session logging |
| **Parental Guard** | ✅ | Pause immediately when child leaves frame |

---

## 📁 Project Structure

```
.
├── head/
│   ├── stream_attention.py       # Single viewer — all features
│   ├── stream_attention_2p.py    # Two-viewer mode
│   ├── stream_attention_4p.py    # Four-viewer weighted group mode
│   ├── analytics.py              # Session event logger
│   ├── platforms.py              # Multi-OTT platform config
│   ├── sessions/                 # Session JSON files saved here
│   └── face_landmarker.task      # MediaPipe model (downloaded separately)
├── dashboard.html                # Interactive analytics dashboard
├── serve_dashboard.py            # Serves dashboard with real session data
├── stream_seek_test.py          # CLI playback control tool
├── requirements.txt
└── README.md
```

---

## 🚀 Setup

### 1. Clone

```bash
git clone https://github.com/Suj1234/attention-aware-player-enhanced.git
cd attention-aware-player-enhanced
```

### 2. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Download MediaPipe Model

```bash
curl -L -o head/face_landmarker.task \
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
```

### 5. macOS Permissions

Go to **System Settings → Privacy & Security**:
- **Camera** — allow Terminal
- **Automation** — allow Terminal to control Google Chrome

### 5. Windows Only

```bash
# Close all Chrome windows first, then:
start chrome.exe --remote-debugging-port=9222
# Open your streaming platform in that Chrome window
```

---

## ▶️ Running

### Single Viewer — Auto-detects platform

```bash
python head/stream_attention.py
```

### Force a specific platform

```bash
python head/stream_attention.py --platform prime
python head/stream_attention.py --platform youtube
python head/stream_attention.py --platform hotstar
python head/stream_attention.py --platform jiocinema
python head/stream_attention.py --platform appletv
python head/stream_attention.py --platform netflix
```

### Special modes

```bash
# Use phone as WiFi webcam instead of built-in camera
python head/stream_attention.py --camera-url http://192.168.1.100:8080/video

# Skip rewind if you were absent for more than 30s (intentional break)
python head/stream_attention.py --smart-seek

# Strict attention thresholds + Pomodoro 25/5 session logging
python head/stream_attention.py --study-mode

# Pause immediately when face leaves frame (child/parental mode)
python head/stream_attention.py --parental

# Open analytics dashboard automatically on quit
python head/stream_attention.py --dashboard
```

### Multi-Person Viewing

```bash
python head/stream_attention_2p.py                    # 2 viewers, both must watch
python head/stream_attention_4p.py                    # up to 4 viewers, weighted
python head/stream_attention_4p.py --threshold 0.75   # stricter: 75% must be watching
```

### Analytics Dashboard

```bash
# Always use serve_dashboard.py — do NOT open dashboard.html directly
python serve_dashboard.py
```

> ⚠️ Opening `dashboard.html` via `file://` shows only demo data. Run `serve_dashboard.py` to inject your real session data.

### CLI Playback Control

```bash
python stream_seek_test.py --play
python stream_seek_test.py --pause
python stream_seek_test.py --get-time
python stream_seek_test.py --minutes 18 --seconds 30
```

---

## 🧠 How It Works

### Head Pose Estimation
1. **Digital zoom** — crops centre of frame so MediaPipe works at distance
2. **478-point face mesh** — MediaPipe Face Landmarker returns 3D landmarks
3. **Yaw & Pitch** — computed from cross-product of ear-to-ear and top-to-bottom vectors
4. **Auto-calibration** — first 30 frames set neutral baseline

### State Machine
```
WATCHING   → face visible, head pointing at screen
AWAY       → face visible, head turned (pauses after 1.5s grace)
ABSENT     → face left camera (plays on; seeks back on return)
DROWSY     → face visible + EAR < 0.25 for 2s (pauses + alert)
```

### Drowsiness Detection (EAR)
```
EAR = (|p2-p6| + |p3-p5|) / (2 × |p1-p4|)
```
Where p1–p6 are the 6 MediaPipe eye landmark points per eye.  
`EAR < 0.25` for 2+ seconds → **DROWSY** state triggered.

### Emotion Detection (Blendshapes)
| Emotion | Trigger | Action |
|---|---|---|
| Confused | `browInnerUp > 0.6` | Auto-rewind 15 seconds |
| Surprised | `eyeWideLeft/Right > 0.7` | Bookmark timestamp |
| Bored | 3+ away events in 2 min | Skip forward 30 seconds |

### Platform JS Injection
- **macOS**: AppleScript → `execute tab javascript` in Google Chrome
- **Windows**: Chrome DevTools Protocol (CDP) via WebSocket on port 9222
- Smart video element selection — picks the active playing element when multiple `<video>` elements exist (e.g. Prime Video)
- Netflix uses its internal JS API; YouTube uses `movie_player` API; all others use HTML5 `<video>` element

---

## 📊 Analytics Dashboard

```bash
python serve_dashboard.py    # serves at http://localhost:8765, auto-opens browser
```

Shows:
- 📈 Weekly attention trend line
- 🍩 Session breakdown donut (watching / away / absent / drowsy)
- ⏱ Colour-coded attention timeline per episode
- 📋 Full session history table with distraction counts

Session data is saved to `head/sessions/YYYY-MM-DD_HH-MM.json`.

---

## ⚙️ Configuration

Key constants at the top of each script:

| Constant | Default | Description |
|---|---|---|
| `CAMERA_INDEX` | `0` | Webcam device index |
| `YAW_THRESHOLD_DEG` | `25` | Max horizontal head turn allowed |
| `PITCH_THRESHOLD_DEG` | `20` | Max vertical head tilt allowed |
| `AWAY_GRACE_SEC` | `1.5` | Seconds before pause triggers |
| `BACK_GRACE_SEC` | `0.8` | Seconds before resume triggers |
| `DIGITAL_ZOOM` | `2.0` | Crop zoom for distant faces |
| `EAR_THRESHOLD` | `0.25` | Eye openness threshold for drowsiness |
| `DROWSY_GRACE_SEC` | `2.0` | Seconds of low EAR before drowsy alert |
| `SMART_SEEK_MAX_SEC` | `30.0` | Max absent duration to trigger rewind |
| `ATTENTION_THRESHOLD` | `0.5` | Group attention threshold (4P mode) |

---

## 🔧 Troubleshooting

**`No tab found in Chrome`** — Make sure your streaming platform is open in Chrome (not another browser).

**`AppleScript error`** — Grant Terminal access in System Settings → Privacy → Automation.

**`Model not found`** — Re-run the `curl` download command for `face_landmarker.task`.

**Bad detection at distance** — Increase `DIGITAL_ZOOM` to `3.0`.

**Drowsy too sensitive** — Raise `EAR_THRESHOLD` to `0.20` or increase `DROWSY_GRACE_SEC`.

**Dashboard shows demo data** — Use `python serve_dashboard.py` instead of opening `dashboard.html` directly.

---

## 🌐 Supported Platforms

| Platform | URL Filter | Control Method |
|---|---|---|
| Netflix | `netflix.com` | Netflix internal JS API |
| Prime Video | `primevideo.com` | HTML5 `<video>` (smart element selection) |
| YouTube | `youtube.com` | `movie_player` JS API |
| Disney+ Hotstar | `hotstar.com` | HTML5 `<video>` element |
| JioCinema | `jiocinema.com` | HTML5 `<video>` element |
| Apple TV+ | `tv.apple.com` | HTML5 `<video>` element |

---

## 🛠 Tech Stack

| Tool | Purpose |
|---|---|
| Google MediaPipe | Face landmark detection (478 points + blendshapes) |
| OpenCV | Webcam feed, frame processing, HUD overlay |
| Python 3.10+ | Core runtime |
| AppleScript / CDP | JS injection into Chrome (macOS / Windows) |
| Chart.js | Analytics dashboard charts |
| scipy | Signal smoothing utilities |

---

## 📄 License

MIT License — see [LICENSE](./LICENSE) for details.
