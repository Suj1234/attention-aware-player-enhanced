"""
Netflix Attention Controller — Enhanced Edition
================================================
Features added over the original Tuesday-Labs base:
  - Drowsiness Detection (Eye Aspect Ratio / EAR)
  - Session event logging → analytics.py
  - Multi-OTT platform support → platforms.py
  - Smart audio fade on AWAY
  - Emotion-aware playback (blendshapes)
  - --dashboard flag to open analytics on quit
  - --platform flag for manual platform override

Original behaviour:
  HEAD TURNED AWAY  → Netflix PAUSES after AWAY_GRACE_SEC seconds.
  FACE ABSENT       → Netflix KEEPS PLAYING; seeks back on return.
  DROWSY            → Pauses + plays alert sound.

Controls:  q = Quit
"""

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import FaceLandmarkerOptions, FaceLandmarker
import numpy as np
import math
import time
import os
import subprocess
import sys
import threading
import argparse
from collections import deque

# ─────────────────────────── CONFIG ─────────────────────────── #
CAMERA_INDEX        = 0
FILTER_LENGTH       = 10
YAW_THRESHOLD_DEG   = 25
PITCH_THRESHOLD_DEG = 20
AUTO_CALIB_FRAMES   = 30
DIGITAL_ZOOM        = 2.0
AWAY_GRACE_SEC      = 1.5
BACK_GRACE_SEC      = 0.8
MIN_ABSENT_FOR_SEEK_SEC = 2.0
REWIND_DISPLAY_SEC  = 2.0

# ── Drowsiness Detection (EAR) ──────────────────────────────── #
EAR_THRESHOLD    = 0.25   # Eye openness below this = drowsy
DROWSY_GRACE_SEC = 2.0    # Seconds of low EAR before triggering drowsy
LEFT_EYE_IDX  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_IDX = [33, 160, 158, 133, 153, 144]
ALERT_SOUND   = "/System/Library/Sounds/Ping.aiff"

# ── Emotion-Aware Playback (Blendshapes) ────────────────────── #
CONFUSION_REWIND_MS  = 15000   # ms to rewind when confused
BORED_SKIP_MS        = 30000   # ms to skip when bored
BORED_AWAY_WINDOW    = 120     # seconds window for boredom check
BORED_AWAY_THRESHOLD = 3       # # of away events in window = bored

# ── Smart Audio Fade ─────────────────────────────────────────── #
FADE_STEPS = 10

# ── Smart Seek-Back ───────────────────────────────────────────── #
SMART_SEEK_MAX_SEC   = 30.0   # With --smart-seek: only rewind if absent < this

# ── Study Mode ───────────────────────────────────────────────── #
STUDY_YAW_THRESHOLD  = 15     # Stricter yaw  (vs normal 25°)
STUDY_PITCH_THRESHOLD= 15     # Stricter pitch (vs normal 20°)
STUDY_AWAY_GRACE_SEC = 0.5    # Shorter grace  (vs normal 1.5s)
POMODORO_WORK_SEC    = 25 * 60
POMODORO_BREAK_SEC   = 5  * 60

# ── Parental Attention Guard ──────────────────────────────────── #
PARENTAL_ABSENT_GRACE= 3.0    # Seconds before pausing when child leaves frame

# ── Head pose landmark indices ──────────────────────────────── #
HEAD_LANDMARKS = {"left": 234, "right": 454, "top": 10, "bottom": 152, "front": 1}

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "face_landmarker.task"
)

# ─────────────────────── PLATFORM SUPPORT ───────────────────── #
def _get_platform_config(platform_name: str) -> dict:
    """Return JS strings and URL filter for the given platform."""
    from platforms import PLATFORMS
    return PLATFORMS.get(platform_name, PLATFORMS["netflix"])


def _auto_detect_platform() -> str:
    """Scan Chrome tabs on macOS and return the first matched platform."""
    try:
        from platforms import PLATFORMS
        script = '''
        tell application "Google Chrome"
            set allUrls to ""
            repeat with w in windows
                repeat with t in tabs of w
                    try
                        set allUrls to allUrls & (URL of t) & "|"
                    end try
                end repeat
            end repeat
            return allUrls
        end tell
        '''
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        urls_raw = r.stdout.strip().lower()
        if not urls_raw:
            print("[Platform] No Chrome tabs detected.")
            return "netflix"

        for name, cfg in PLATFORMS.items():
            if cfg["url_filter"] in urls_raw:
                print(f"[Platform] ✅ Auto-detected: {name.upper()}")
                return name
        
        print(f"[Platform] No supported OTT found in tabs. Defaulting to Netflix logic.")
    except Exception as e:
        print(f"[Platform] Auto-detect failed: {e}")
    return "netflix"


# ─────────────────────── JS INJECTION ───────────────────────── #
def _nf_js(action: str) -> str:
    return (
        f"(function(){{"
        f"  try {{"
        f"    var _vids = document.querySelectorAll('video');"
        f"    var v = null;"
        f"    for (var _j = 0; _j < _vids.length; _j++) {{"
        f"      if (_vids[_j].src !== '' || _vids[_j].currentTime > 0) {{ v = _vids[_j]; break; }}"
        f"    }}"
        f"    if (!v) v = _vids.length > 0 ? _vids[0] : null;"
        f"    if (!v) {{"
        f"      var frames = document.querySelectorAll('iframe');"
        f"      for (var _i = 0; _i < frames.length; _i++) {{"
        f"        try {{ var _fv = frames[_i].contentDocument.querySelector('video'); if (_fv) {{ v = _fv; break; }} }} catch(_e) {{}}"
        f"      }}"
        f"    }}"
        f"    var pl = null;"
        f"    /* 1. Try Netflix API */"
        f"    var n = window.netflix || window.__netflix || (typeof netflix !== 'undefined' ? netflix : null);"
        f"    if (n) {{"
        f"      try {{"
        f"        var vp = n.appContext.state.playerApp.getAPI().videoPlayer;"
        f"        var ids = vp.getAllPlayerSessionIds();"
        f"        if (ids.length > 0) pl = vp.getVideoPlayerBySessionId(ids[0]);"
        f"      }} catch(e) {{}}"
        f"    }}"
        f"    /* 2. Try YouTube API */"
        f"    var yt = document.getElementById('movie_player') || document.querySelector('.html5-video-player');"
        f"    if (!pl && yt && yt.pauseVideo) pl = yt;"
        f""
        f"    if (!pl && !v) return 'ERROR:No video or player API found';"
        f""
        f"    var _play = function() {{ "
        f"       if (pl && pl.playVideo) pl.playVideo(); "
        f"       else if (pl && pl.play) pl.play(); "
        f"       else if (v) v.play(); "
        f"    }};"
        f"    var _pause = function() {{ "
        f"       if (pl && pl.pauseVideo) pl.pauseVideo(); "
        f"       else if (pl && pl.pause) pl.pause(); "
        f"       else if (v) v.pause(); "
        f"    }};"
        f"    var _getTime = function() {{ "
        f"       if (pl && pl.getCurrentTime) return pl.getCurrentTime() * 1000; "
        f"       return v ? v.currentTime * 1000 : 0; "
        f"    }};"
        f"    var _seek = function(m) {{ "
        f"       if (pl && pl.seekTo) pl.seekTo(m/1000); "
        f"       else if (pl && pl.seek) pl.seek(m); "
        f"       else if (v) v.currentTime = m/1000; "
        f"    }};"
        f"    var _getVol = function() {{ return v ? v.volume : 1.0; }};"
        f"    var _setVol = function(vol) {{ if(v) v.volume = Math.max(0, Math.min(1, vol)); }};"
        f"    {action.replace('pl.play()', '_play()').replace('pl.pause()', '_pause()').replace('pl.getCurrentTime()', '_getTime()').replace('pl.seek', '_seek').replace('_getVolume()', '_getVol()').replace('_setVolume', '_setVol')}"
        f"  }} catch (e) {{ return 'ERROR:' + e.message; }}"
        f"}})();"
    )


_JS_PLAY     = _nf_js("_play();  return 'PLAYING';")
_JS_PAUSE    = _nf_js("_pause(); return 'PAUSED';")
_JS_GET_TIME = _nf_js("return 'TIME:' + _getTime();")


def _inject_and_read_mac(inner_js: str, url_filter: str = "netflix.com") -> str:
    def esc(js: str) -> str:
        return js.replace("\\", "\\\\").replace('"', '\\"')

    script = f'''
    tell application "Google Chrome"
        set foundTab to false
        set resultVal to "NO_TAB"
        repeat with w in windows
            repeat with t in tabs of w
                try
                    set theUrl to (URL of t) as string
                    if theUrl contains "{url_filter}" then
                        set resultVal to execute t javascript "{esc(inner_js)}"
                        set foundTab to true
                        exit repeat
                    end if
                end try
            end repeat
            if foundTab then exit repeat
        end repeat
        return resultVal as string
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    output = result.stdout.strip()
    if output == "NO_TAB":
        raise RuntimeError(f"No tab matching '{url_filter}' found in Chrome — open the streaming page first!")
    return output


def _inject_and_read_windows(inner_js: str, url_filter: str = "netflix.com") -> str:
    import urllib.request, json
    try:
        import websocket
    except ImportError:
        raise RuntimeError("Please install: pip install -r requirements.txt")
    try:
        req = urllib.request.Request("http://127.0.0.1:9222/json")
        with urllib.request.urlopen(req) as response:
            tabs = json.loads(response.read().decode())
        tab = next((t for t in tabs if 'url' in t and url_filter in t['url'].lower()), None)
        if not tab:
            raise RuntimeError(f"No tab matching '{url_filter}' found in Chrome")
        ws_url = tab.get('webSocketDebuggerUrl')
        if not ws_url:
            raise RuntimeError("Tab found but no WebSocket URL (Chrome not debuggable?)")
        ws = websocket.create_connection(ws_url)
        payload = {"id": 1, "method": "Runtime.evaluate",
                   "params": {"expression": inner_js, "returnByValue": True}}
        ws.send(json.dumps(payload))
        result = json.loads(ws.recv())
        ws.close()
        if 'result' in result and 'result' in result['result']:
            val = result['result']['result'].get('value')
            if val is not None:
                return str(val)
        return "ERROR: CDP Evaluation failed"
    except urllib.error.URLError:
        raise RuntimeError("Could not connect to Chrome — start it with --remote-debugging-port=9222")


def _inject_and_read(inner_js: str, url_filter: str = "netflix.com") -> str:
    import platform
    if platform.system() == "Windows":
        return _inject_and_read_windows(inner_js, url_filter)
    else:
        return _inject_and_read_mac(inner_js, url_filter)


# Shared url_filter used by all control functions (set in main)
_URL_FILTER = "netflix.com"

def _netflix_play():
    try:
        r = _inject_and_read(_JS_PLAY, _URL_FILTER)
        print(f"[Player] ▶  Play  → {r}")
    except Exception as e:
        print(f"[Player] Play failed: {e}")

def _netflix_pause():
    try:
        r = _inject_and_read(_JS_PAUSE, _URL_FILTER)
        print(f"[Player] ⏸  Pause → {r}")
    except Exception as e:
        print(f"[Player] Pause failed: {e}")

_last_good_ms: float = -1.0
_last_good_at: float = 0.0

def _netflix_get_time_ms() -> float:
    global _last_good_ms, _last_good_at
    try:
        r = _inject_and_read(_JS_GET_TIME, _URL_FILTER)
        if r.startswith("TIME:"):
            val = float(r[5:])
            _last_good_ms = val
            _last_good_at = time.time()
            return val
        raise RuntimeError(r)
    except Exception as e:
        print(f"[Player] Get-time failed: {e}")
        if _last_good_ms >= 0:
            estimated = _last_good_ms + (time.time() - _last_good_at) * 1000
            print(f"[Player] Using estimated position: {estimated/1000:.1f}s")
            return estimated
        return -1.0

def _netflix_seek_ms(ms: int):
    try:
        js = _nf_js(f"_seek({int(ms)}); return 'SEEKED_TO:{int(ms)}';")
        r = _inject_and_read(js, _URL_FILTER)
        print(f"[Player] ⏩ Seek  → {r}")
    except Exception as e:
        print(f"[Player] Seek failed: {e}")

def _set_volume(vol: float):
    """Set browser video volume (0.0 – 1.0)."""
    try:
        js = _nf_js(f"_setVol({vol:.3f}); return 'VOL:{vol:.3f}';")
        _inject_and_read(js, _URL_FILTER)
    except Exception:
        pass


# ─────────────────────── AUDIO FADE ─────────────────────────── #
def _fade_volume(start: float, end: float, duration: float):
    """Smoothly fade volume from start→end over duration seconds (background thread)."""
    steps = FADE_STEPS
    delay = duration / steps
    for i in range(steps + 1):
        t = i / steps
        vol = start + (end - start) * t
        _set_volume(vol)
        time.sleep(delay)


def start_fade(start: float, end: float, duration: float):
    th = threading.Thread(target=_fade_volume, args=(start, end, duration), daemon=True)
    th.start()


# ───────────────────── DROWSINESS / EAR ─────────────────────── #
def _dist(p1, p2) -> float:
    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)

def compute_ear(lms, eye_indices, x1, y1, x2, y2) -> float:
    """Compute Eye Aspect Ratio for the given 6-point eye landmark indices."""
    def pt(idx):
        lm = lms[idx]
        return (x1 + lm.x * (x2 - x1), y1 + lm.y * (y2 - y1))
    p = [pt(i) for i in eye_indices]
    # EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
    ear = (_dist(p[1], p[5]) + _dist(p[2], p[4])) / (2.0 * max(_dist(p[0], p[3]), 1e-6))
    return ear


def play_alert():
    """Play macOS system alert sound in a background thread."""
    def _play():
        try:
            subprocess.run(["afplay", ALERT_SOUND], check=False)
        except Exception:
            pass
    threading.Thread(target=_play, daemon=True).start()


# ─────────────────── BLENDSHAPE / EMOTION ───────────────────── #
def extract_blendshapes(result) -> dict:
    """Extract blendshape name→score dict from MediaPipe result."""
    if not result.face_blendshapes or not result.face_blendshapes[0]:
        return {}
    return {bs.category_name: bs.score for bs in result.face_blendshapes[0]}


# ─────────────────────── DISPLAY HELPERS ────────────────────── #
def fmt_duration(sec):
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"

def compute_head_confidence(yaw_off, pitch_off):
    yw = max(0.0, 1.0 - abs(yaw_off)   / YAW_THRESHOLD_DEG)
    pt = max(0.0, 1.0 - abs(pitch_off) / PITCH_THRESHOLD_DEG)
    return (yw + pt) / 2.0

def draw_overlay(frame, is_looking, head_ok,
                 yaw_off, pitch_off, head_conf,
                 session_elapsed, looking_s, away_s,
                 face_visible, calib_done,
                 drowsy=False, emotion="", platform_name="netflix"):
    """Draw compact info panel — now includes DROWSY label, emotion, and platform."""
    h, w = frame.shape[:2]
    pw, ph = 260, 230
    px, py = w - pw - 15, 15
    ov = frame.copy()
    cv2.rectangle(ov, (px, py), (px + pw, py + ph), (15, 15, 25), -1)
    cv2.addWeighted(ov, 0.78, frame, 0.22, 0, frame)

    if drowsy:
        border = (0, 100, 255)       # orange-red for drowsy
    elif not face_visible:
        border = (180, 140, 0)
    elif is_looking:
        border = (50, 220, 80)
    else:
        border = (50, 80, 230)
    cv2.rectangle(frame, (px, py), (px + pw, py + ph), border, 2)

    fx, fy = px + 12, py + 22
    plat_label = platform_name.upper()
    cv2.putText(frame, f"VIEWER  [{plat_label}]", (fx, fy),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (160, 200, 255), 1, cv2.LINE_AA)
    fy += 24

    if drowsy:
        cv2.putText(frame, "⚠  DROWSY", (fx, fy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.70, (0, 80, 255), 2, cv2.LINE_AA)
        fy += 28
    elif not face_visible:
        cv2.putText(frame, "NOT IN FRAME",
                    (fx, fy), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (30, 170, 220), 1, cv2.LINE_AA)
        fy += 40
    else:
        if not calib_done:
            cv2.putText(frame, "CALIBRATING…",
                        (fx, fy), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (200, 200, 50), 1, cv2.LINE_AA)
        else:
            txt = "LOOKING" if is_looking else "AWAY"
            col = (50, 230, 80) if is_looking else (50, 80, 230)
            cv2.putText(frame, txt, (fx, fy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, col, 2, cv2.LINE_AA)
        fy += 22

        bw   = pw - 24
        fill = int(bw * head_conf)
        bc   = (50, 200, 80) if head_conf > 0.6 else (50, 120, 200) if head_conf > 0.3 else (60, 60, 210)
        cv2.putText(frame, f"Confidence: {int(head_conf * 100)}%",
                    (fx, fy), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1, cv2.LINE_AA)
        fy += 12
        cv2.rectangle(frame, (fx, fy), (fx + bw, fy + 10), (40, 40, 60), -1)
        cv2.rectangle(frame, (fx, fy), (fx + fill, fy + 10), bc, -1)
        fy += 24

        cv2.putText(frame, f"Yaw: {yaw_off:+.1f}  Pitch: {pitch_off:+.1f}",
                    (fx, fy), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (140, 180, 240), 1, cv2.LINE_AA)
        fy += 20

    if emotion:
        cv2.putText(frame, f"Emotion: {emotion}",
                    (fx, fy), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 200, 100), 1, cv2.LINE_AA)
        fy += 18

    total = max(looking_s + away_s, 1)
    pct   = int(looking_s / total * 100)
    cv2.putText(frame, f"Look {fmt_duration(looking_s)} ({pct}%)  Away {fmt_duration(away_s)}",
                (fx, fy), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (180, 170, 120), 1, cv2.LINE_AA)


def draw_status_bar(frame, netflix_paused, is_looking, seek_back_secs,
                    session_elapsed, drowsy=False):
    h, w = frame.shape[:2]
    ov = frame.copy()
    cv2.rectangle(ov, (0, h - 42), (w, h), (15, 15, 25), -1)
    cv2.addWeighted(ov, 0.82, frame, 0.18, 0, frame)

    nf_txt = "Player: PAUSED" if netflix_paused else "Player: PLAYING"
    nf_col = (80, 80, 230) if netflix_paused else (50, 230, 80)
    cv2.putText(frame, nf_txt, (12, h - 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, nf_col, 1, cv2.LINE_AA)

    if drowsy:
        badge, badge_col = "⚠ DROWSY!", (0, 80, 255)
    elif is_looking:
        badge, badge_col = "WATCHING", (50, 230, 80)
    else:
        badge, badge_col = "ATTENTION LOST", (50, 80, 230)
    cv2.putText(frame, badge, (w // 2 - 75, h - 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, badge_col, 1, cv2.LINE_AA)

    if seek_back_secs > 0:
        cv2.putText(frame, f"↩ Seek-back: {fmt_duration(seek_back_secs)}",
                    (w - 220, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (30, 200, 255), 1, cv2.LINE_AA)

    cv2.putText(frame, f"Session {fmt_duration(session_elapsed)}  [q]=quit",
                (12, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (120, 120, 120), 1, cv2.LINE_AA)


# ──────────────────────────── MAIN ──────────────────────────── #
def main():
    global _URL_FILTER

    parser = argparse.ArgumentParser(description="Attention-Aware Player — Enhanced Edition")
    parser.add_argument("--platform", default=None,
                        help="Force platform: netflix|prime|youtube|hotstar|jiocinema|appletv")
    parser.add_argument("--dashboard", action="store_true",
                        help="Open analytics dashboard on quit")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Unused in 1P mode (for 4P mode use stream_attention_4p.py)")
    parser.add_argument("--camera-url", default=None,
                        help="WiFi camera URL, e.g. http://192.168.1.100:8080/video (IP Webcam app)")
    parser.add_argument("--smart-seek", action="store_true",
                        help="Only rewind if absent < 30s — skips seek-back for intentional breaks")
    parser.add_argument("--study-mode", action="store_true",
                        help="Stricter thresholds + Pomodoro logging (25min work / 5min break)")
    parser.add_argument("--parental", action="store_true",
                        help="Pause when face leaves frame (child mode) instead of seeking back")
    args = parser.parse_args()

    # ── Platform detection ──────────────────────────────────────
    if args.platform:
        platform_name = args.platform.lower()
    else:
        platform_name = _auto_detect_platform()
    print(f"[Platform] Using: {platform_name}")

    try:
        from platforms import PLATFORMS
        platform_cfg = PLATFORMS.get(platform_name, PLATFORMS["netflix"])
        _URL_FILTER = platform_cfg["url_filter"]
    except ImportError:
        _URL_FILTER = "netflix.com"
        platform_cfg = {"url_filter": "netflix.com"}

    # ── Study mode: override thresholds ─────────────────────────
    if args.study_mode:
        global YAW_THRESHOLD_DEG, PITCH_THRESHOLD_DEG, AWAY_GRACE_SEC
        YAW_THRESHOLD_DEG    = STUDY_YAW_THRESHOLD
        PITCH_THRESHOLD_DEG  = STUDY_PITCH_THRESHOLD
        AWAY_GRACE_SEC       = STUDY_AWAY_GRACE_SEC
        print("[Study Mode] Thresholds tightened. Pomodoro timer active.")

    # ── Analytics ───────────────────────────────────────────────
    try:
        from analytics import SessionLogger
        logger = SessionLogger(platform=platform_name)
    except ImportError:
        logger = None

    # ── State vars ──────────────────────────────────────────────
    calib_yaw = calib_pitch = 0.0
    ray_origins    = deque(maxlen=FILTER_LENGTH)
    ray_directions = deque(maxlen=FILTER_LENGTH)
    auto_calib_done    = False
    auto_calib_yaws    = []
    auto_calib_pitches = []

    session_start   = time.time()
    looking_seconds = 0.0
    away_seconds    = 0.0
    last_tick       = time.time()

    netflix_paused    = False
    away_since        = None
    back_since        = None
    face_visible      = True
    face_absent_since = None
    rewinding_until   = 0.0

    # ── Drowsiness state ────────────────────────────────────────
    drowsy_since      = None     # wall-clock when EAR first went below threshold
    is_drowsy         = False
    alert_played      = False

    # ── Emotion state ───────────────────────────────────────────
    current_emotion   = ""
    away_event_times  = []       # for boredom detection
    last_confusion_action = 0.0
    last_boredom_action   = 0.0

    # ── Study mode / Pomodoro ─────────────────────────────────── #
    pomo_block_start  = time.time()   # when current block began
    pomo_block_num    = 0             # 0=work,1=break,2=work,…
    pomo_block_look   = 0.0           # looking_seconds within this block
    pomo_log: list[dict] = []         # completed block summaries

    # ── Model setup ─────────────────────────────────────────────
    if not os.path.exists(MODEL_PATH):
        print(f"[ERROR] Model not found: {MODEL_PATH}")
        print("  Expected at:", MODEL_PATH)
        sys.exit(1)

    base_opts = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    opts = FaceLandmarkerOptions(
        base_options=base_opts,
        output_face_blendshapes=True,           # ← Enabled for emotion detection
        output_facial_transformation_matrixes=False,
        num_faces=1,
        min_face_detection_confidence=0.3,
        min_face_presence_confidence=0.3,
        min_tracking_confidence=0.3,
        running_mode=mp_vision.RunningMode.VIDEO,
    )
    detector = FaceLandmarker.create_from_options(opts)

    camera_source = args.camera_url if args.camera_url else CAMERA_INDEX
    if args.camera_url:
        print(f"[Camera] Using WiFi stream: {args.camera_url}")
    cap = cv2.VideoCapture(camera_source)
    if not cap.isOpened():
        src_desc = args.camera_url or f"camera index {CAMERA_INDEX}"
        print(f"[ERROR] Cannot open {src_desc}.")
        if args.camera_url:
            print("  Make sure IP Webcam (Android) or similar is running and the URL is reachable.")
        sys.exit(1)

    print("=" * 58)
    print("  Attention-Aware Player — Enhanced Edition")
    print(f"  Platform : {platform_name.upper()}")
    print("  Auto-calibrating on first face detection…")
    print("  LOOK AWAY → pauses | DROWSY → pauses + alert")
    print("  LEAVE FRAME → seeks back on return")
    print("  q = quit" + ("  | --dashboard = open analytics on quit" if args.dashboard else ""))
    print("=" * 58)

    frame_idx = 0
    raw_yaw = raw_pitch = 180.0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        fh, fw = frame.shape[:2]

        # ── Digital zoom ─────────────────────────────────────────
        if DIGITAL_ZOOM != 1.0:
            cx, cy   = fw // 2, fh // 2
            crop_w   = int(fw / DIGITAL_ZOOM)
            crop_h   = int(fh / DIGITAL_ZOOM)
            x1 = max(cx - crop_w // 2, 0)
            y1 = max(cy - crop_h // 2, 0)
            x2 = min(x1 + crop_w, fw)
            y2 = min(y1 + crop_h, fh)
            det_frame = cv2.resize(frame[y1:y2, x1:x2], (fw, fh),
                                   interpolation=cv2.INTER_LINEAR)
        else:
            x1 = y1 = 0
            x2, y2 = fw, fh
            det_frame = frame

        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB,
                          data=cv2.cvtColor(det_frame, cv2.COLOR_BGR2RGB))
        ts_ms  = int(cap.get(cv2.CAP_PROP_POS_MSEC)) or (frame_idx * 33)
        result = detector.detect_for_video(mp_img, ts_ms)
        frame_idx += 1

        now       = time.time()
        dt        = now - last_tick
        last_tick = now
        session_elapsed = now - session_start

        is_looking = head_ok = False
        yaw_off = pitch_off = 0.0
        head_conf = 0.0
        face_in_frame = bool(result.face_landmarks)

        # ── Face came BACK ───────────────────────────────────────
        if face_in_frame and not face_visible:
            face_visible = True
            if face_absent_since is not None:
                absent_duration = now - face_absent_since
                if logger:
                    logger.log("back", _netflix_get_time_ms())
                if absent_duration >= MIN_ABSENT_FOR_SEEK_SEC:
                    if args.smart_seek and absent_duration >= SMART_SEEK_MAX_SEC:
                        print(f"[Smart Seek-Back] Absent {absent_duration:.1f}s — likely intentional break, not rewinding.")
                    else:
                        print(f"[Seek-Back] Absent {absent_duration:.1f}s — seeking back…")
                        current_ms = _netflix_get_time_ms()
                        if current_ms >= 0:
                            seek_target = max(0, current_ms - absent_duration * 1000)
                            _netflix_seek_ms(int(seek_target))
                            rewinding_until = now + REWIND_DISPLAY_SEC
                face_absent_since = None
            away_since = back_since = None
            drowsy_since = None
            is_drowsy = False
            alert_played = False

        # ── Face DISAPPEARED ─────────────────────────────────────
        elif not face_in_frame and face_visible:
            face_visible = False
            face_absent_since = now
            if args.parental:
                print(f"[Parental] Face left frame — will pause in {PARENTAL_ABSENT_GRACE:.0f}s.")
            else:
                print("[Absent] Face left frame — timer started, player keeps playing.")
                if netflix_paused:
                    _netflix_play()
                    netflix_paused = False
            if logger:
                logger.log("absent", _netflix_get_time_ms())
            away_since = back_since = None
            drowsy_since = None
            is_drowsy = False
            alert_played = False

        # ── Head pose + drowsiness + emotion (face in frame) ─────
        if face_in_frame:
            lms = result.face_landmarks[0]

            def lm_np(idx):
                lm = lms[idx]
                crop_w = x2 - x1; crop_h = y2 - y1
                return np.array([x1 + lm.x * crop_w, y1 + lm.y * crop_h, lm.z * crop_w])

            left   = lm_np(HEAD_LANDMARKS["left"])
            right  = lm_np(HEAD_LANDMARKS["right"])
            top    = lm_np(HEAD_LANDMARKS["top"])
            bottom = lm_np(HEAD_LANDMARKS["bottom"])
            front  = lm_np(HEAD_LANDMARKS["front"])

            r_ax = right - left;  r_ax /= np.linalg.norm(r_ax)
            u_ax = top - bottom;  u_ax /= np.linalg.norm(u_ax)
            fwd  = np.cross(r_ax, u_ax); fwd /= np.linalg.norm(fwd); fwd = -fwd

            center = (left + right + top + bottom + front) / 5.0
            ray_origins.append(center)
            ray_directions.append(fwd)

            avg_dir = np.mean(ray_directions, axis=0)
            avg_dir /= np.linalg.norm(avg_dir)
            avg_origin = np.mean(ray_origins, axis=0)

            xz = np.array([avg_dir[0], 0.0, avg_dir[2]])
            if np.linalg.norm(xz) > 1e-6: xz /= np.linalg.norm(xz)
            yaw_rad = math.acos(np.clip(np.dot([0.0, 0.0, -1.0], xz), -1.0, 1.0))
            if avg_dir[0] < 0: yaw_rad = -yaw_rad
            yaw_deg = np.degrees(yaw_rad)
            yaw_deg = abs(yaw_deg) if yaw_deg < 0 else (360 - yaw_deg if yaw_deg < 180 else yaw_deg)
            raw_yaw = yaw_deg

            yz = np.array([0.0, avg_dir[1], avg_dir[2]])
            if np.linalg.norm(yz) > 1e-6: yz /= np.linalg.norm(yz)
            pitch_rad = math.acos(np.clip(np.dot([0.0, 0.0, -1.0], yz), -1.0, 1.0))
            if avg_dir[1] > 0: pitch_rad = -pitch_rad
            pitch_deg = np.degrees(pitch_rad)
            pitch_deg = 360 + pitch_deg if pitch_deg < 0 else pitch_deg
            raw_pitch = pitch_deg

            # ── Auto-calibration ─────────────────────────────────
            if not auto_calib_done:
                auto_calib_yaws.append(raw_yaw)
                auto_calib_pitches.append(raw_pitch)
                n = len(auto_calib_yaws)
                bw_bar = int(fw * (n / AUTO_CALIB_FRAMES))
                cv2.rectangle(frame, (0, fh - 8), (bw_bar, fh), (50, 200, 255), -1)
                cv2.putText(frame, f"Auto-calibrating… {n}/{AUTO_CALIB_FRAMES}",
                            (10, fh - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (50, 200, 255), 1, cv2.LINE_AA)
                if n >= AUTO_CALIB_FRAMES:
                    calib_yaw   = 180.0 - float(np.mean(auto_calib_yaws))
                    calib_pitch = 180.0 - float(np.mean(auto_calib_pitches))
                    auto_calib_done = True
                    print(f"[Auto-Calibrated] Yaw={calib_yaw:.1f}  Pitch={calib_pitch:.1f}")

            yaw_off   = (raw_yaw   + calib_yaw)   - 180.0
            pitch_off = (raw_pitch + calib_pitch)  - 180.0
            head_conf = max(0.0, min(1.0, compute_head_confidence(yaw_off, pitch_off)))
            head_ok   = (auto_calib_done and
                         abs(yaw_off)   <= YAW_THRESHOLD_DEG and
                         abs(pitch_off) <= PITCH_THRESHOLD_DEG)
            is_looking = head_ok

            # ── EAR / Drowsiness ─────────────────────────────────
            left_ear  = compute_ear(lms, LEFT_EYE_IDX,  x1, y1, x2, y2)
            right_ear = compute_ear(lms, RIGHT_EYE_IDX, x1, y1, x2, y2)
            avg_ear   = (left_ear + right_ear) / 2.0

            if avg_ear < EAR_THRESHOLD and is_looking:
                # Eyes closing but head still facing screen
                if drowsy_since is None:
                    drowsy_since = now
                elif (now - drowsy_since) >= DROWSY_GRACE_SEC:
                    is_drowsy = True
                    if not alert_played:
                        print("[DROWSY] Alert triggered!")
                        play_alert()
                        alert_played = True
                    if not netflix_paused:
                        _netflix_pause()
                        netflix_paused = True
                        if logger:
                            logger.log("drowsy", _netflix_get_time_ms())
            else:
                if is_drowsy and avg_ear >= EAR_THRESHOLD:
                    # User woke up
                    is_drowsy    = False
                    alert_played = False
                    drowsy_since = None
                    print("[DROWSY] Eyes open — resuming.")
                elif not is_drowsy:
                    drowsy_since = None

            # ── Blendshapes / Emotion ────────────────────────────
            blendshapes = extract_blendshapes(result)
            current_emotion = ""

            if blendshapes:
                brow_inner = blendshapes.get("browInnerUp", 0.0)
                eye_wide_l = blendshapes.get("eyeWideLeft", 0.0)
                eye_wide_r = blendshapes.get("eyeWideRight", 0.0)

                if brow_inner > 0.6 and auto_calib_done:
                    current_emotion = "Confused"
                    if now - last_confusion_action > 10.0:
                        print("[Emotion] Confused detected — rewinding 15s")
                        current_ms = _netflix_get_time_ms()
                        if current_ms >= 0:
                            _netflix_seek_ms(int(max(0, current_ms - CONFUSION_REWIND_MS)))
                        last_confusion_action = now
                        if logger:
                            logger.log("confused_rewind", current_ms)

                elif (eye_wide_l + eye_wide_r) / 2 > 0.7:
                    current_emotion = "Surprised!"
                    if logger:
                        logger.log("surprised_bookmark", _netflix_get_time_ms())

                # Boredom: 3+ away events in last 2 minutes
                now_ts = time.time()
                away_event_times[:] = [t for t in away_event_times if now_ts - t < BORED_AWAY_WINDOW]
                if len(away_event_times) >= BORED_AWAY_THRESHOLD:
                    current_emotion = "Bored"
                    if now - last_boredom_action > 60.0:
                        print("[Emotion] Bored detected — skipping 30s")
                        current_ms = _netflix_get_time_ms()
                        if current_ms >= 0:
                            _netflix_seek_ms(int(current_ms + BORED_SKIP_MS))
                        away_event_times.clear()
                        last_boredom_action = now
                        if logger:
                            logger.log("bored_skip", current_ms)

            # ── Draw landmarks + gaze ray ─────────────────────────
            half_w  = np.linalg.norm(right - left) / 2
            ray_end = avg_origin - avg_dir * (2.5 * half_w)
            ray_col = (50, 230, 80) if head_ok else (50, 80, 230)
            cv2.line(frame, (int(avg_origin[0]), int(avg_origin[1])),
                     (int(ray_end[0]), int(ray_end[1])), ray_col, 3)
            for lm in lms:
                dot_x = int(x1 + lm.x * (x2 - x1))
                dot_y = int(y1 + lm.y * (y2 - y1))
                cv2.circle(frame, (dot_x, dot_y), 1, (40, 120, 40), -1)
            for iris_idx in [468, 473]:
                ilm = lms[iris_idx]
                ix, iy = int(x1 + ilm.x * (x2 - x1)), int(y1 + ilm.y * (y2 - y1))
                cv2.circle(frame, (ix, iy), 3, (255, 255, 255), -1)
                cv2.circle(frame, (ix, iy), 6, (0, 255, 255), 1)

            # EAR display
            ear_col = (0, 80, 255) if avg_ear < EAR_THRESHOLD else (80, 220, 80)
            cv2.putText(frame, f"EAR: {avg_ear:.3f}", (12, fh - 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, ear_col, 1, cv2.LINE_AA)

        # ── Time tracking ─────────────────────────────────────────
        if is_looking:
            looking_seconds += dt
            if args.study_mode:
                pomo_block_look += dt
        else:
            away_seconds += dt

        # ── Study Mode: Pomodoro tick ──────────────────────────────
        if args.study_mode:
            is_work_block = (pomo_block_num % 2 == 0)
            block_duration = POMODORO_WORK_SEC if is_work_block else POMODORO_BREAK_SEC
            if (now - pomo_block_start) >= block_duration:
                block_secs  = now - pomo_block_start
                block_attn  = int(pomo_block_look / max(block_secs, 1) * 100)
                block_type  = "Work" if is_work_block else "Break"
                pomo_log.append({"block": pomo_block_num + 1, "type": block_type,
                                 "duration_sec": int(block_secs), "attention_pct": block_attn})
                next_type = "Break" if is_work_block else "Work"
                print(f"[Pomodoro] {block_type} block {pomo_block_num + 1} complete — "
                      f"{block_attn}% attention. Starting {next_type} block.")
                if logger:
                    logger.log(f"pomodoro_{block_type.lower()}_complete",
                               _netflix_get_time_ms())
                pomo_block_num   += 1
                pomo_block_start  = now
                pomo_block_look   = 0.0

        # ── Parental: pause after grace period when face absent ───
        if args.parental and not face_visible and face_absent_since is not None:
            if (now - face_absent_since) >= PARENTAL_ABSENT_GRACE and not netflix_paused:
                print("[Parental] Pausing — child left frame.")
                _netflix_pause()
                netflix_paused = True
                if logger:
                    logger.log("parental_pause", _netflix_get_time_ms())

        # ── Pause / Play (only when face visible and not drowsy) ──
        if auto_calib_done and face_visible and not is_drowsy:
            if not is_looking:
                back_since = None
                if away_since is None:
                    away_since = now
                    away_event_times.append(now)       # track for boredom
                    if logger:
                        pos = _netflix_get_time_ms()
                        logger.log("away", pos) if pos >= 0 else None
                elif (now - away_since) >= AWAY_GRACE_SEC and not netflix_paused:
                    # Smart audio fade before pause
                    start_fade(1.0, 0.0, AWAY_GRACE_SEC * 0.5)
                    time.sleep(AWAY_GRACE_SEC * 0.5)
                    _netflix_pause()
                    netflix_paused = True
            else:
                away_since = None
                if back_since is None:
                    back_since = now
                    if logger:
                        pos = _netflix_get_time_ms()
                        logger.log("looking", pos) if pos >= 0 else None
                elif (now - back_since) >= BACK_GRACE_SEC and netflix_paused:
                    _netflix_play()
                    start_fade(0.0, 1.0, BACK_GRACE_SEC)
                    netflix_paused = False

        # ── Greyscale while rewinding ─────────────────────────────
        is_rewinding = (now < rewinding_until)
        if is_rewinding:
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)

        # ── Overlay drawing ───────────────────────────────────────
        absent_secs = (now - face_absent_since) if face_absent_since else 0.0
        draw_overlay(frame, is_looking, head_ok,
                     yaw_off, pitch_off, head_conf,
                     session_elapsed, looking_seconds, away_seconds,
                     face_visible, auto_calib_done,
                     drowsy=is_drowsy, emotion=current_emotion,
                     platform_name=platform_name)
        draw_status_bar(frame, netflix_paused, is_looking, absent_secs,
                        session_elapsed, drowsy=is_drowsy)

        if is_rewinding:
            rw_text = "REWINDING"
            rw_scale, rw_thick = 1.8, 4
            (tw, th), _ = cv2.getTextSize(rw_text, cv2.FONT_HERSHEY_SIMPLEX, rw_scale, rw_thick)
            rx, ry = (fw - tw) // 2, (fh + th) // 2
            cv2.rectangle(frame, (rx - 16, ry - th - 12), (rx + tw + 16, ry + 12), (0, 0, 0), -1)
            cv2.putText(frame, rw_text, (rx, ry),
                        cv2.FONT_HERSHEY_SIMPLEX, rw_scale, (0, 0, 255), rw_thick, cv2.LINE_AA)

        cv2.imshow("Attention-Aware Player — Enhanced", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

    # ── Cleanup ───────────────────────────────────────────────────
    detector.close()
    cap.release()
    cv2.destroyAllWindows()

    total = looking_seconds + away_seconds
    pct   = int(looking_seconds / total * 100) if total > 0 else 0
    print("\n── Session Summary ─────────────────────────────────")
    print(f"  Platform : {platform_name.upper()}")
    print(f"  Total    : {fmt_duration(total)}")
    print(f"  Looking  : {fmt_duration(looking_seconds)}  ({pct}%)")
    print(f"  Away     : {fmt_duration(away_seconds)}  ({100 - pct}%)")
    if args.study_mode and pomo_log:
        print("  ── Pomodoro Log ──────────────────────────────────")
        for b in pomo_log:
            mins = b["duration_sec"] // 60
            print(f"    Block {b['block']:>2} [{b['type']:<5}] {mins:>3}min  {b['attention_pct']}% attention")
    print("────────────────────────────────────────────────────\n")

    # ── Save analytics session ────────────────────────────────────
    if logger:
        session_file = logger.save(
            title="Unknown Title",
            duration_sec=int(total),
            attention_pct=pct
        )
        print(f"[Analytics] Session saved → {session_file}")

        if args.dashboard:
            dashboard_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "dashboard.html"
            )
            if os.path.exists(dashboard_path):
                subprocess.Popen(["open", dashboard_path])
                print(f"[Dashboard] Opened → {dashboard_path}")


if __name__ == "__main__":
    main()