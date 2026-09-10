# Infosys GestureVolume: Pinch Gesture -> System Audio Volume Control
# Highly optimized, low-latency, accurate two-hand gesture controller.
# Windows only. Requires: pip install pycaw comtypes mediapipe opencv-python Pillow matplotlib

import sys, os, time, threading, queue, platform, traceback, math
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision
import matplotlib
matplotlib.use('TkAgg')
import tkinter as tk
from PIL import Image, ImageTk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from collections import deque
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)

if platform.system() != "Windows":
    raise SystemExit("This script runs only on Windows.")

# --- Model path ---
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")
if not os.path.exists(MODEL_PATH):
    raise SystemExit(f"Model file not found: {MODEL_PATH}")

# --- Config ---
CAM_INDEX             = 0
CAM_WIDTH             = 640
CAM_HEIGHT            = 480
MIN_PINCH_RATIO       = 0.18   # Pinching thumb tip & index tip together -> 0% volume (Mute)
MAX_PINCH_RATIO       = 0.90   # Open / spread thumb & index fingers -> 100% volume

# ── Zero-Latency Asynchronous PyCAW Volume Controller ──────────────────
from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize, GUID
from comtypes.client import CreateObject
from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
from ctypes import cast, POINTER

class AsyncVolumeController(threading.Thread):
    """
    Dedicated COM background worker that handles Windows master audio endpoint
    volume get/set operations with zero blocking on the main camera/UI thread.
    """
    def __init__(self, target_mode="speaker"):
        super().__init__(daemon=True)
        self.cmd_queue = queue.Queue()
        self.running = True
        self.target_mode = target_mode.lower()
        self.current_volume = 50
        self.ready_event = threading.Event()
        self.lock = threading.Lock()

    def run(self):
        CoInitialize()
        try:
            clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
            enum = CreateObject(clsid, interface=IMMDeviceEnumerator)
        except Exception:
            enum = CreateObject("MMDeviceEnumerator.MMDeviceEnumerator", interface=IMMDeviceEnumerator)

        def _activate_endpoint(mode):
            flow = 1 if mode == "mic" else 0
            dev = enum.GetDefaultAudioEndpoint(flow, 0)
            iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(iface, POINTER(IAudioEndpointVolume))

        vol_iface = None
        try:
            vol_iface = _activate_endpoint(self.target_mode)
            val = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
            with self.lock:
                self.current_volume = max(0, min(100, val))
        except Exception as ex:
            print(f"[COM] Primary init failed for {self.target_mode}: {ex}. Falling back to speaker.")
            try:
                self.target_mode = "speaker"
                vol_iface = _activate_endpoint("speaker")
                val = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
                with self.lock:
                    self.current_volume = max(0, min(100, val))
            except Exception as e2:
                print(f"[COM] Fatal endpoint error: {e2}")

        self.ready_event.set()

        while self.running:
            try:
                cmd, arg = self.cmd_queue.get(timeout=0.03)
                if cmd == "set" and vol_iface is not None:
                    target_scalar = max(0.0, min(1.0, arg / 100.0))
                    vol_iface.SetMasterVolumeLevelScalar(target_scalar, None)
                    with self.lock:
                        self.current_volume = arg
                elif cmd == "target":
                    mode = str(arg).lower()
                    try:
                        new_iface = _activate_endpoint(mode)
                        vol_iface = new_iface
                        val = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
                        with self.lock:
                            self.target_mode = mode
                            self.current_volume = max(0, min(100, val))
                    except Exception as e:
                        print(f"[COM] Switch to {mode} failed: {e}")
                elif cmd == "quit":
                    break
            except queue.Empty:
                # Periodic sync query every ~500ms when idle
                if vol_iface is not None:
                    try:
                        val = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
                        with self.lock:
                            self.current_volume = max(0, min(100, val))
                    except: pass
            except Exception as e:
                print(f"[COM Loop Error]: {e}")

        try: CoUninitialize()
        except: pass

    def set_volume(self, percent):
        p = max(0, min(100, int(percent)))
        with self.lock:
            self.current_volume = p
        # Drain older pending volume commands to avoid lag build-up
        while not self.cmd_queue.empty():
            try:
                item = self.cmd_queue.get_nowait()
                if item[0] != "set":
                    self.cmd_queue.put(item)
            except: break
        self.cmd_queue.put(("set", p))

    def get_volume(self):
        with self.lock:
            return self.current_volume

    def set_target(self, mode):
        mode = mode.lower()
        self.cmd_queue.put(("target", mode))
        time.sleep(0.05)
        return self.get_volume()

    def stop(self):
        self.running = False
        self.cmd_queue.put(("quit", None))
        self.join(timeout=1.0)


# Initialize async volume controller
initial_target = "speaker"
if len(sys.argv) > 1 and sys.argv[1].lower() in ("speaker", "mic"):
    initial_target = sys.argv[1].lower()

vol_ctrl = AsyncVolumeController(target_mode=initial_target)
vol_ctrl.start()
vol_ctrl.ready_event.wait(timeout=2.0)


# ── High-Precision Landmark Smoothing Filter ───────────────────────────
class LandmarkFilter:
    """Adaptive smoothing filter: zero-jitter when still, zero-latency when moving."""
    def __init__(self, min_alpha=0.40, max_alpha=0.90):
        self.min_alpha = min_alpha
        self.max_alpha = max_alpha
        self.prev_val = None

    def update(self, val):
        if self.prev_val is None or val is None:
            self.prev_val = val
            return val
        
        if isinstance(val, (tuple, list)):
            dist = math.hypot(val[0] - self.prev_val[0], val[1] - self.prev_val[1])
            factor = min(1.0, dist / 18.0)
            alpha = self.min_alpha + factor * (self.max_alpha - self.min_alpha)
            smooth = (
                alpha * val[0] + (1.0 - alpha) * self.prev_val[0],
                alpha * val[1] + (1.0 - alpha) * self.prev_val[1]
            )
            self.prev_val = smooth
            return smooth
        else:
            dist = abs(val - self.prev_val)
            factor = min(1.0, dist / 0.12)
            alpha = self.min_alpha + factor * (self.max_alpha - self.min_alpha)
            smooth = alpha * val + (1.0 - alpha) * self.prev_val
            self.prev_val = smooth
            return smooth

    def reset(self):
        self.prev_val = None


# ── Camera Stream (Threaded Capture) ───────────────────────────────────
class CameraStream:
    def __init__(self, cam_index=0, width=640, height=480):
        try:
            self.cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        except Exception:
            self.cap = cv2.VideoCapture(cam_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.ret = False
        self.frame = None
        self.running = True
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.ret = ret
                    self.frame = frame
            else:
                time.sleep(0.005)

    def read(self):
        with self.lock:
            if self.frame is None:
                return False, None
            return self.ret, self.frame.copy()

    def release(self):
        self.running = False
        try: self.thread.join(timeout=0.5)
        except: pass
        try: self.cap.release()
        except: pass


# ── MediaPipe Hand Landmarker (VIDEO Mode for Temporal Tracking) ───────
base_options = mp_tasks.BaseOptions(model_asset_path=MODEL_PATH)
landmarker_options = mp_vision.HandLandmarkerOptions(
    base_options=base_options,
    running_mode=mp_vision.RunningMode.VIDEO,
    num_hands=2,
    min_hand_detection_confidence=0.5,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5
)
hand_landmarker = mp_vision.HandLandmarker.create_from_options(landmarker_options)


def is_left_index_pointing_up(lm_list):
    """
    Checks if Left Hand has ONLY the index finger extended / pointing up,
    while Middle, Ring, Pinky, and Thumb are folded.
    """
    if not lm_list or len(lm_list) < 21:
        return False

    # 1. Index finger tip (8) is raised significantly above index PIP (6)
    index_raised = (lm_list[8].y < lm_list[6].y - 0.04) and (lm_list[8].y < lm_list[5].y)

    # 2. Middle (12), Ring (16), Pinky (20) tips are folded (below their PIP joints)
    middle_folded = lm_list[12].y >= lm_list[10].y - 0.02
    ring_folded   = lm_list[16].y >= lm_list[14].y - 0.02
    pinky_folded  = lm_list[20].y >= lm_list[18].y - 0.02

    # 3. Thumb folded
    try:
        thumb_folded = abs(lm_list[4].x - lm_list[9].x) < abs(lm_list[2].x - lm_list[9].x) + 0.06
    except Exception:
        thumb_folded = True

    return index_raised and middle_folded and ring_folded and pinky_folded and thumb_folded


def draw_hand_skeleton(frame, landmarks, color, label_text):
    """Draws hand skeleton connections and wrist badge."""
    img_h, img_w = frame.shape[:2]
    connections = [
        (0,1),(1,2),(2,3),(3,4),
        (0,5),(5,6),(6,7),(7,8),
        (5,9),(9,10),(10,11),(11,12),
        (9,13),(13,14),(14,15),(15,16),
        (13,17),(17,18),(18,19),(19,20),(0,17)
    ]
    pts = [(int(p.x * img_w), int(p.y * img_h)) for p in landmarks]
    for a, b in connections:
        try: cv2.line(frame, pts[a], pts[b], color, 2, cv2.LINE_AA)
        except: pass
    for pt in pts:
        try:
            cv2.circle(frame, pt, 3, (255, 255, 255), -1, cv2.LINE_AA)
        except: pass
        
    if pts:
        wrist_x, wrist_y = pts[0]
        text_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
        text_y = max(20, wrist_y - 8)
        text_rect_y1 = max(0, wrist_y - 24)
        text_rect_y2 = max(25, wrist_y + 4)
        cv2.rectangle(frame, (wrist_x - 6, text_rect_y1), (wrist_x + text_size[0] + 6, text_rect_y2), (10, 10, 10), -1)
        cv2.rectangle(frame, (wrist_x - 6, text_rect_y1), (wrist_x + text_size[0] + 6, text_rect_y2), color, 1)
        cv2.putText(frame, label_text, (wrist_x - 2, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


# ── Tkinter Application ───────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title("Infosys_GestureVolume: Gesture -> Audio Volume")
        root.geometry("1280x780")
        root.configure(bg="#111")
        root.resizable(True, False)
        root.rowconfigure(0, weight=0); root.rowconfigure(1, weight=1); root.rowconfigure(2, weight=0)
        root.columnconfigure(0, weight=1)

        # Filters for Right Hand Thumb First Point (4) & Index First Point (8)
        self.thumb_filter = LandmarkFilter()
        self.index_filter = LandmarkFilter()
        self.pinch_filter = LandmarkFilter()

        # Header
        hdr = tk.Frame(root, bg="#181818", height=70)
        hdr.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        hdr.grid_propagate(False)
        tk.Label(hdr, text="Infosys_GestureVolume: Volume Control with Hand Gestures",
                 font=("Consolas", 18, "bold"), fg="#00ffcc", bg="#181818",
                 anchor="e", padx=20).pack(fill=tk.X, pady=(5, 0))
        tk.Label(hdr,
                 text="Project made by BATCH A | SNEHIL GHOSH, GAUTAM N CHIPKAR, AMRUTHA VARSHANI, AYUSH GORGE",
                 font=("Consolas", 11), fg="#cccccc", bg="#181818",
                 anchor="e", padx=20).pack(fill=tk.X, pady=(0, 5))

        # Main Layout
        main = tk.Frame(root, bg="#111")
        main.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        main.columnconfigure(0, weight=1); main.columnconfigure(1, weight=3)
        main.rowconfigure(0, weight=1)

        # Graph Panel
        self.graph_panel = tk.Frame(main, bg="#181818")
        self.graph_panel.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        for i in range(3): self.graph_panel.rowconfigure(i, weight=1)

        # Video Panel
        self.video_label = tk.Label(main, bg="#000")
        self.video_label.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

        # Status Bar
        self.status_label = tk.Label(root, text="Starting camera...",
                                      bg="#111", fg="#00ff99",
                                      font=("Consolas", 12), anchor="w", padx=10)
        self.status_label.grid(row=2, column=0, sticky="ew", pady=(0, 4))

        # Graph 1 — Live distances
        self.fig1 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax1  = self.fig1.add_subplot(111)
        self.ax1.set_facecolor("#111"); self.ax1.tick_params(colors="white")
        self.ax1.set_title("LIVE PINCH RATIO (Thumb Tip to Index Tip)", color="white", fontsize=8)
        self.line_ratio, = self.ax1.plot([], [], label="Pinch Ratio", color="#00ffcc", linewidth=1.5)
        self.ax1.set_ylim(-0.05, 1.2)
        self.canvas1 = FigureCanvasTkAgg(self.fig1, master=self.graph_panel)
        self.canvas1.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Graph 2 — Aspect Ratio
        self.fig2 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax2  = self.fig2.add_subplot(111)
        self.ax2.set_facecolor("#111"); self.ax2.tick_params(colors="white")
        self.ax2.set_title("Hand Aspect Ratio", color="white", fontsize=8)
        self.line_aspect, = self.ax2.plot([], [], color="#ffcc00", linewidth=1.5)
        self.ax2.set_ylim(0.2, 2.0)
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=self.graph_panel)
        self.canvas2.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        # Graph 3 — Volume Level
        self.fig3 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax3  = self.fig3.add_subplot(111)
        self.ax3.set_facecolor("#111"); self.ax3.tick_params(colors="white")
        self.ax3.set_title("Active System Volume %", color="white", fontsize=8)
        self.line_vol, = self.ax3.plot([], [], color="#00ff88", linewidth=1.5)
        self.ax3.set_ylim(-5, 105)
        self.canvas3 = FigureCanvasTkAgg(self.fig3, master=self.graph_panel)
        self.canvas3.get_tk_widget().grid(row=2, column=0, sticky="nsew")

        # Camera & State
        self.cap = CameraStream(CAM_INDEX, CAM_WIDTH, CAM_HEIGHT)
        self.running = True
        self.current_volume = vol_ctrl.get_volume()
        self.target_mode = vol_ctrl.target_mode

        # Two-Hand Lock / Unlock States
        self.lock_state = False
        self.left_index_consecutive_true = 0
        self.left_index_consecutive_false = 0
        self.last_lock_toggle_time = 0.0
        self.hud_message = ""
        self.hud_message_time = 0.0

        # Performance & Plot History
        self.timestamps    = deque(maxlen=120)
        self.ratio_history = deque(maxlen=120)
        self.aspect_history= deque(maxlen=120)
        self.vol_history   = deque(maxlen=120)
        self.start_time    = time.time()
        self.last_plot_time= 0.0
        self.frame_count   = 0
        self.last_fps_time = time.time()
        self.fps           = 0.0
        self.frame_timestamp_ms = 0

        root.bind("<Key>", self._on_keypress)
        root.protocol("WM_DELETE_WINDOW", self.stop_and_close)
        self._update_frame()

    def toggle_target_mode(self):
        new_mode = "mic" if self.target_mode == "speaker" else "speaker"
        self.current_volume = vol_ctrl.set_target(new_mode)
        self.target_mode = new_mode
        self.hud_message = f"TARGET: {self.target_mode.upper()}"
        self.hud_message_time = time.time()

    def update_left_hand_lock(self, left_landmarks):
        """
        Detects left hand index finger pointing up to toggle Lock/Unlock state.
        Uses debouncing and cooldown for crisp, reliable toggling without fluttering.
        """
        is_pointing = is_left_index_pointing_up(left_landmarks)
        now = time.time()

        if is_pointing:
            self.left_index_consecutive_true += 1
            self.left_index_consecutive_false = 0
            # Trigger on 3 stable frames if cooldown (0.7s) has passed
            if self.left_index_consecutive_true == 3 and (now - self.last_lock_toggle_time >= 0.7):
                self.lock_state = not self.lock_state
                self.last_lock_toggle_time = now
                self.hud_message = "VOLUME LOCKED" if self.lock_state else "VOLUME UNLOCKED"
                self.hud_message_time = now
        else:
            self.left_index_consecutive_false += 1
            if self.left_index_consecutive_false >= 3:
                self.left_index_consecutive_true = 0

    def update_right_hand_volume(self, right_landmarks, img_w, img_h):
        """
        Calculates volume using Right Hand Thumb First Point (Landmark 4)
        and Index Finger First Point (Landmark 8) in 2D scale-invariant space.
        """
        if not right_landmarks:
            self.thumb_filter.reset()
            self.index_filter.reset()
            self.pinch_filter.reset()
            return None, None, False, self.current_volume, None, None

        # 1. Right Hand Thumb First Point (Tip 4) & Index First Point (Tip 8)
        raw_tx = right_landmarks[4].x * img_w
        raw_ty = right_landmarks[4].y * img_h
        raw_ix = right_landmarks[8].x * img_w
        raw_iy = right_landmarks[8].y * img_h

        # 2. Adaptive low-pass filter on first points to eliminate webcam jitter
        st = self.thumb_filter.update((raw_tx, raw_ty))
        si = self.index_filter.update((raw_ix, raw_iy))
        tx_px, ty_px = int(round(st[0])), int(round(st[1]))
        ix_px, iy_px = int(round(si[0])), int(round(si[1]))

        # 3. Reference palm size (Wrist 0 to Middle MCP 9, Index MCP 5 to Pinky MCP 17)
        wx = right_landmarks[0].x * img_w
        wy = right_landmarks[0].y * img_h
        mx = right_landmarks[9].x * img_w
        my = right_landmarks[9].y * img_h
        idx_mcp_x = right_landmarks[5].x * img_w
        idx_mcp_y = right_landmarks[5].y * img_h
        pnk_mcp_x = right_landmarks[17].x * img_w
        pnk_mcp_y = right_landmarks[17].y * img_h

        palm_len = math.hypot(mx - wx, my - wy)
        palm_width = math.hypot(idx_mcp_x - pnk_mcp_x, idx_mcp_y - pnk_mcp_y)
        ref_size = max(palm_len, palm_width, 25.0)

        # 4. Euclidean 2D pixel distance between thumb first point and index first point
        pixel_dist = math.hypot(tx_px - ix_px, ty_px - iy_px)

        # 5. Normalized scale-invariant pinch ratio
        raw_ratio = pixel_dist / ref_size
        smooth_ratio = self.pinch_filter.update(raw_ratio)

        # 6. Pinch state & direct volume mapping
        is_pinched = smooth_ratio <= (MIN_PINCH_RATIO + 0.04)
        target_vol = int(np.clip(np.interp(smooth_ratio, [MIN_PINCH_RATIO, MAX_PINCH_RATIO], [0, 100]), 0, 100))

        # 7. Apply volume if unlocked (via non-blocking async COM worker)
        if not self.lock_state:
            if target_vol != self.current_volume:
                vol_ctrl.set_volume(target_vol)
                self.current_volume = target_vol
        else:
            self.current_volume = vol_ctrl.get_volume()

        return smooth_ratio, pixel_dist, is_pinched, self.current_volume, (tx_px, ty_px), (ix_px, iy_px)

    def process_frame(self, frame):
        """Processes hand tracking, lock status, volume control, and visual graphics."""
        img_h, img_w = frame.shape[:2]
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        self.frame_timestamp_ms += 33
        result = None
        try:
            result = hand_landmarker.detect_for_video(mp_image, self.frame_timestamp_ms)
        except Exception:
            try:
                # Fallback timestamp if needed
                result = hand_landmarker.detect_for_video(mp_image, int(time.time() * 1000))
            except: pass

        # Sort hands deterministically from left to right on screen
        hands = []
        if result and result.hand_landmarks:
            for idx, landmarks in enumerate(result.hand_landmarks):
                wrist_x = landmarks[0].x
                hands.append((wrist_x, landmarks))
        hands.sort(key=lambda item: item[0])

        left_landmarks = None
        right_landmarks = None
        left_detected = False
        right_detected = False

        if len(hands) == 1:
            lm = hands[0][1]
            # Single-hand smart support: if pointing index up -> Lock/Unlock; else -> Volume control
            if is_left_index_pointing_up(lm) and not self.lock_state:
                left_landmarks = lm
                left_detected = True
            else:
                right_landmarks = lm
                right_detected = True
        elif len(hands) >= 2:
            left_landmarks = hands[0][1]
            left_detected = True
            right_landmarks = hands[1][1]
            right_detected = True

        # Update left hand lock toggle
        self.update_left_hand_lock(left_landmarks)

        # Update right hand volume using thumb tip (4) and index tip (8)
        pinch_ratio, pixel_dist, is_pinched, self.current_volume, thumb_pt, index_pt = self.update_right_hand_volume(
            right_landmarks, img_w, img_h
        )

        # ── 1. Draw Left Hand (Cyan) ──────────────────────────────────
        if left_detected and left_landmarks:
            draw_hand_skeleton(frame, left_landmarks, (255, 255, 0), "Left Hand: Lock Toggle")
            if is_left_index_pointing_up(left_landmarks):
                ix_px = int(round(left_landmarks[8].x * img_w))
                iy_px = int(round(left_landmarks[8].y * img_h))
                cv2.circle(frame, (ix_px, iy_px), 16, (0, 255, 0), 2, cv2.LINE_AA)
                cv2.putText(frame, "LOCK TRIGGER", (ix_px - 40, iy_px - 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

        # ── 2. Draw Right Hand (Volume Control with First Points) ─────
        if right_detected and right_landmarks and thumb_pt and index_pt:
            role_text = f"Right Hand: Volume Control ({'LOCKED' if self.lock_state else 'ACTIVE'})"
            draw_hand_skeleton(frame, right_landmarks, (255, 0, 255), role_text)

            tx_px, ty_px = thumb_pt
            ix_px, iy_px = index_pt

            # Dynamic line and badge color based on volume / lock state
            if self.lock_state:
                line_color = (0, 0, 220)       # Red outline when locked
            elif self.current_volume < 15:
                line_color = (0, 80, 255)      # Orange/Red when pinched (0-15%)
            elif self.current_volume < 60:
                line_color = (0, 215, 255)     # Yellow in mid range
            else:
                line_color = (0, 255, 128)     # Bright Neon Green (60-100%)

            # Draw prominent glowing circles on Thumb First Point (4) and Index First Point (8)
            cv2.circle(frame, (tx_px, ty_px), 10, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(frame, (tx_px, ty_px), 7, (0, 120, 255), -1, cv2.LINE_AA)
            cv2.putText(frame, "Thumb (1st)", (tx_px - 35, ty_px + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

            cv2.circle(frame, (ix_px, iy_px), 10, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(frame, (ix_px, iy_px), 7, (255, 0, 180), -1, cv2.LINE_AA)
            cv2.putText(frame, "Index (1st)", (ix_px - 30, iy_px - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

            # Draw connecting pinch line between first points
            cv2.line(frame, (tx_px, ty_px), (ix_px, iy_px), line_color, 3, cv2.LINE_AA)

            # Midpoint Volume Badge
            mid_x = (tx_px + ix_px) // 2
            mid_y = (ty_px + iy_px) // 2
            cv2.circle(frame, (mid_x, mid_y), 6, (255, 255, 255), -1, cv2.LINE_AA)

            badge_text = f"{self.current_volume}%" if not is_pinched else "0% (PINCHED)"
            if self.lock_state:
                badge_text += " [LOCKED]"
            t_size = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_DUPLEX, 0.65, 2)[0]
            bx = mid_x - t_size[0] // 2
            by = max(30, mid_y - 20)
            cv2.rectangle(frame, (bx - 8, by - 20), (bx + t_size[0] + 8, by + 6), (15, 15, 15), -1)
            cv2.rectangle(frame, (bx - 8, by - 20), (bx + t_size[0] + 8, by + 6), line_color, 1)
            cv2.putText(frame, badge_text, (bx, by), cv2.FONT_HERSHEY_DUPLEX, 0.65, line_color, 2, cv2.LINE_AA)

        # Aspect ratio of active hand
        aspect_ratio = None
        if right_detected and right_landmarks:
            xs = [p.x for p in right_landmarks]
            ys = [p.y for p in right_landmarks]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            if height > 0:
                aspect_ratio = width / height

        # ── 3. On-Screen Vertical Volume Gauge (Right Side) ───────────
        bar_x1, bar_x2 = img_w - 55, img_w - 25
        bar_y1, bar_y2 = 90, img_h - 60
        bar_height = bar_y2 - bar_y1
        fill_h = int(bar_height * (self.current_volume / 100.0))

        # Bar background
        cv2.rectangle(frame, (bar_x1, bar_y1), (bar_x2, bar_y2), (20, 20, 20), -1)
        # Bar fill
        fill_color = (0, 0, 220) if self.lock_state else ((0, 255, 200) if self.current_volume > 30 else (0, 160, 255))
        cv2.rectangle(frame, (bar_x1, bar_y2 - fill_h), (bar_x2, bar_y2), fill_color, -1)
        # Bar outline
        cv2.rectangle(frame, (bar_x1, bar_y1), (bar_x2, bar_y2), (0, 255, 200) if not self.lock_state else (0, 0, 255), 2)

        vol_str = f"{self.current_volume}%"
        v_size = cv2.getTextSize(vol_str, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
        cv2.putText(frame, vol_str, (bar_x1 + (30 - v_size[0]) // 2, bar_y2 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 200), 2, cv2.LINE_AA)

        # ── 4. HUD Text Overlay ───────────────────────────────────────
        target_label = "Speaker" if self.target_mode == "speaker" else "Microphone"
        lines = [
            f"Left Hand (Lock):  {'DETECTED' if left_detected else 'NOT DETECTED'}",
            f"Right Hand (Pinch):{'DETECTED' if right_detected else 'NOT DETECTED'}",
            f"Audio Target:      {target_label} (Press 'M' to switch)",
            f"System Volume:     {self.current_volume}%",
            f"Control State:     {'LOCKED' if self.lock_state else 'ACTIVE'}"
        ]
        for i, txt in enumerate(lines):
            color = (255, 255, 255)
            if "NOT DETECTED" in txt or "LOCKED" in txt:
                color = (0, 0, 255)
            elif "DETECTED" in txt or "ACTIVE" in txt:
                color = (0, 255, 0)
            elif "Volume" in txt or "Target" in txt:
                color = (255, 255, 0)

            cv2.putText(frame, txt, (10, 30 + i*28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

        # ── 5. State Toggle Alert Banner ──────────────────────────────
        if self.hud_message and (time.time() - self.hud_message_time < 2.0):
            text_size = cv2.getTextSize(self.hud_message, cv2.FONT_HERSHEY_DUPLEX, 1.1, 2)[0]
            text_x = (img_w - text_size[0]) // 2
            text_y = 110
            
            rect_color = (0, 0, 180) if "LOCKED" in self.hud_message else (0, 160, 0)
            cv2.rectangle(frame, (text_x - 18, text_y - 32), (text_x + text_size[0] + 18, text_y + 12), rect_color, -1)
            cv2.rectangle(frame, (text_x - 18, text_y - 32), (text_x + text_size[0] + 18, text_y + 12), (255, 255, 255), 2)
            cv2.putText(frame, self.hud_message, (text_x, text_y),
                        cv2.FONT_HERSHEY_DUPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)

        return pinch_ratio, pixel_dist, is_pinched, aspect_ratio

    def _on_keypress(self, event):
        if hasattr(event, "char") and event.char:
            c = event.char.lower()
            if c == "q":
                self.stop_and_close()
            elif c in ("m", "t"):
                self.toggle_target_mode()

    def _update_frame(self):
        if not self.running: return

        ret, frame = self.cap.read()
        if not ret or frame is None:
            self.root.after(10, self._update_frame)
            return

        now_time = time.time()
        self.frame_count += 1
        if now_time - self.last_fps_time >= 1.0:
            self.fps = self.frame_count / (now_time - self.last_fps_time)
            self.frame_count = 0
            self.last_fps_time = now_time

        # Horizontal mirror flip for intuitive selfie camera interaction
        frame = cv2.flip(frame, 1)
        pinch_ratio, pixel_dist, is_pinched, aspect_ratio = self.process_frame(frame)

        # High-performance OpenCV resizing (avoids PIL Lanczos CPU bottleneck)
        lbl_w = self.video_label.winfo_width() or 960
        lbl_h = self.video_label.winfo_height() or 720
        if lbl_w > 10 and lbl_h > 10:
            resized_bgr = cv2.resize(frame, (lbl_w, lbl_h), interpolation=cv2.INTER_LINEAR)
            frame_rgb = cv2.cvtColor(resized_bgr, cv2.COLOR_BGR2RGB)
        else:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        pil_img = Image.fromarray(frame_rgb)
        imgtk = ImageTk.PhotoImage(pil_img)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

        # Record graph metrics
        elapsed = time.time() - self.start_time
        self.timestamps.append(elapsed)
        self.ratio_history.append(pinch_ratio if pinch_ratio is not None else float("nan"))
        self.aspect_history.append(aspect_ratio if aspect_ratio is not None else float("nan"))
        self.vol_history.append(self.current_volume)

        # Throttled graph rendering (10 FPS) to maintain high video frame rates
        if now_time - self.last_plot_time >= 0.1:
            self.last_plot_time = now_time
            t_arr   = np.array(self.timestamps)
            rat_arr = np.array(self.ratio_history)
            asp_arr = np.array(self.aspect_history)
            vol_arr = np.array(self.vol_history)

            try:
                self.line_ratio.set_data(t_arr, rat_arr)
                self.ax1.set_xlim(max(0, elapsed - 12), elapsed + 0.1)
                self.canvas1.draw_idle()
            except Exception: pass

            try:
                self.line_aspect.set_data(t_arr, asp_arr)
                self.ax2.set_xlim(max(0, elapsed - 12), elapsed + 0.1)
                self.canvas2.draw_idle()
            except Exception: pass

            try:
                self.line_vol.set_data(t_arr, vol_arr)
                self.ax3.set_xlim(max(0, elapsed - 12), elapsed + 0.1)
                self.canvas3.draw_idle()
            except Exception: pass

        # Status Bar
        now = time.strftime("%H:%M:%S")
        pr_str = f"{pinch_ratio:.3f}" if pinch_ratio is not None else "0.000"
        px_str = f"{pixel_dist:.1f}px" if pixel_dist is not None else "0.0px"
        asp_str = f"{aspect_ratio:.2f}" if aspect_ratio is not None else "N/A"
        self.status_label.config(
            text=f"[{now}] | FPS: {self.fps:.1f} | Target: {self.target_mode.upper()} | Pinch Ratio: {pr_str} | Distance: {px_str} | Aspect: {asp_str} | Volume: {self.current_volume}%"
        )
        self.root.after(1, self._update_frame)

    def stop_and_close(self):
        if not getattr(self, "running", False):
            try: self.root.destroy()
            except: pass
            return
        self.running = False
        try: self.cap.release()
        except: pass
        try: hand_landmarker.close()
        except: pass
        try: vol_ctrl.stop()
        except: pass
        try: self.root.destroy()
        except: pass


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    try:
        root.mainloop()
    finally:
        try: vol_ctrl.stop()
        except: pass
        print("Cleanly shut down Gesture Control Hub.")
