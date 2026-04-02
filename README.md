# 👁 Attention-Aware Netflix Player — Enhanced Edition

> **Auto-pause any streaming platform when you look away, fall asleep, or leave the room.**  
> Built on top of [Tuesday-Labs/Attention-Aware-Netflix-Player](https://github.com/Tuesday-Labs/Attention-Aware-Netflix-Player) · MIT License

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![MediaPipe](https://img.shields.io/badge/MediaPipe-Face%20Landmarker-green) ![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-lightgrey) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ What's New in the Enhanced Edition

| Feature | Status | Description |
|---|---|---|
| **Drowsiness Detection** | ✅ | Eye Aspect Ratio (EAR) — pauses + alert if eyes close for 2s |
| **Attention Analytics Dashboard** | ✅ | Session JSON logging + interactive Chart.js dashboard |
| **Multi-OTT Support** | ✅ | Netflix, Prime Video, YouTube, Hotstar, JioCinema, Apple TV+ |
| **Emotion-Aware Playback** | ✅ | Auto-rewind on confusion, skip on boredom, bookmark on surprise |
| **Smart Audio Fade** | ✅ | Gradual volume fade before pause — no jarring hard-cut |
| **4-Person Weighted Attention** | ✅ | Group majority threshold (configurable) for multi-viewer sessions |

---

## 📁 Project Structure

```
.
├── head/
│   ├── netflix_attention.py      # Single viewer — full enhanced edition
│   ├── netflix_attention_2p.py   # Two-viewer (original)
│   ├── netflix_attention_4p.py   # Four-viewer weighted attention (new)
│   └── face_landmarker.task      # MediaPipe model (~3.6 MB, downloaded separately)
├── analytics.py                  # Session event logger
├── platforms.py                  # Multi-OTT URL + JS config
├── dashboard.html                # Interactive analytics dashboard
├── netflix_seek_test.py          # CLI playback control tool
├── sessions/                     # Auto-created; stores session JSON files
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

### Single Viewer (Enhanced) — Auto-detects platform

```bash
python head/netflix_attention.py
```

### Force a specific platform

```bash
python head/netflix_attention.py --platform prime
python head/netflix_attention.py --platform youtube
python head/netflix_attention.py --platform hotstar
```

### Open analytics dashboard after session

```bash
python head/netflix_attention.py --dashboard
```

### 4-Person Group Viewing

```bash
python head/netflix_attention_4p.py                    # 50% majority rule
python head/netflix_attention_4p.py --threshold 0.75  # stricter: 75% must be watching
```

### CLI Playback Control

```bash
python netflix_seek_test.py --play
python netflix_seek_test.py --pause
python netflix_seek_test.py --get-time
python netflix_seek_test.py --minutes 18 --seconds 30
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
DROWSY     → face visible + EAR < 0.25 for 2s (pauses + Ping alert)
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

### Multi-OTT Support
All platforms except Netflix use `document.querySelector('video')`.  
Netflix uses its internal JS API with HTML5 video fallback.  
Platform is **auto-detected** by scanning Chrome tab URLs.

---

## 📊 Analytics Dashboard

After any session, open `dashboard.html` in your browser:

```bash
open dashboard.html
# or automatically on quit:
python head/netflix_attention.py --dashboard
```

Shows:
- 📈 Weekly attention trend line
- 🍩 Session breakdown donut (watching / away / absent / drowsy)
- ⏱ Colour-coded attention timeline per episode
- 📋 Full session history table with distraction counts

Session data is saved to `sessions/YYYY-MM-DD_HH-MM.json`.

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
| `ATTENTION_THRESHOLD` | `0.5` | Group attention threshold (4P mode) |

---

## 🔧 Troubleshooting

**`No tab found in Chrome`** — Make sure your streaming platform is open in Chrome (not another browser).

**`AppleScript error`** — Grant Terminal access in System Settings → Privacy → Automation.

**`Model not found`** — Re-run the `curl` download command for `face_landmarker.task`.

**Bad detection at distance** — Increase `DIGITAL_ZOOM` to `3.0`.

**Drowsy too sensitive** — Raise `EAR_THRESHOLD` to `0.20` or increase `DROWSY_GRACE_SEC`.

---

## 🌐 Supported Platforms

| Platform | URL Filter | Method |
|---|---|---|
| Netflix | `netflix.com` | Netflix JS API + video fallback |
| Prime Video | `primevideo.com` | HTML5 `<video>` element |
| YouTube | `youtube.com` | HTML5 `<video>` element |
| Disney+ Hotstar | `hotstar.com` | HTML5 `<video>` element |
| JioCinema | `jiocinema.com` | HTML5 `<video>` element |
| Apple TV+ | `tv.apple.com` | HTML5 `<video>` element |

---

## 🛠 Tech Stack

| Tool | Purpose |
|---|---|
| Google MediaPipe | Face landmark detection (478 points) |
| OpenCV | Webcam feed, frame processing, HUD overlay |
| Python 3.10+ | Core runtime |
| AppleScript / CDP | JS injection into Chrome |
| Chart.js | Analytics dashboard charts |
| scipy | Signal smoothing utilities |

---

## 📜 Credits

- **Base project**: [Tuesday-Labs/Attention-Aware-Netflix-Player](https://github.com/Tuesday-Labs/Attention-Aware-Netflix-Player) — MIT License  
- **Netflix JS API**: [Stack Overflow answer by Zarbi4734](https://stackoverflow.com/a/61988153) — CC BY-SA 4.0  
- **MediaPipe**: [Google MediaPipe](https://developers.google.com/mediapipe)

---

## 📄 License

MIT License — see [LICENSE](./LICENSE) for details.
