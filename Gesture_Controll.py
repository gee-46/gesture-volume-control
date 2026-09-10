# Infosys GestureVolume: Pinch Gesture -> Mic Volume
# Compatible with mediapipe >= 0.10.x (Tasks API)
# Windows only. Requires: pip install pycaw comtypes mediapipe opencv-python Pillow matplotlib keyboard

import sys, os, time, tempfile, threading, queue, subprocess, platform, traceback, math
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
    raise SystemExit(
        f"Model file not found: {MODEL_PATH}\n"
        "Download it from:\n"
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
    )

# --- Config ---
CAM_INDEX            = 0
CAM_WIDTH            = 640
CAM_HEIGHT           = 480
DEBUG_MODE           = False

# Normalization constants (ratio: pinch_distance / hand_reference_size)
MIN_NORM_PINCH       = 0.12
MAX_NORM_PINCH       = 0.60
VOLUME_STEP_THRESHOLD = 2  # Granular step for smooth control

# --- Child process code (COM/pycaw in isolated process) ---
child_code = r'''
import sys, traceback
def eprint(*a, **k): print(*a, file=sys.stderr, **k)
try:
    from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
    from comtypes.client import CreateObject
    from comtypes import GUID
    from ctypes import POINTER, cast
    from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
except Exception as ex:
    print("ERR IMPORT", ex); eprint("IMPORT TRACEBACK:"); traceback.print_exc(); sys.exit(2)

def out(s): print(s); sys.stdout.flush()

try: CoInitialize()
except Exception as ex: out("ERR COINIT " + str(ex)); sys.exit(3)

def _create_enum():
    from comtypes.client import CreateObject
    from pycaw.pycaw import IMMDeviceEnumerator
    try:
        return CreateObject("MMDeviceEnumerator.MMDeviceEnumerator", interface=IMMDeviceEnumerator)
    except Exception:
        clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        return CreateObject(clsid, interface=IMMDeviceEnumerator)

def _get_vol_iface():
    enumerator = _create_enum()
    import sys
    dataflow = 1
    if len(sys.argv) > 1 and sys.argv[1].lower() == "speaker":
        dataflow = 0
    device = enumerator.GetDefaultAudioEndpoint(dataflow, 0)
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    from ctypes import cast, POINTER
    return cast(iface, POINTER(IAudioEndpointVolume))

try:
    vol = _get_vol_iface()
except Exception as ex:
    out("ERR GET_IFACE " + str(ex))
    try: CoUninitialize()
    except: pass
    sys.exit(4)

out("OK READY")

for raw in sys.stdin:
    line = raw.strip()
    if not line: continue
    try:
        if line.lower() == "get":
            try:
                p = int(round(vol.GetMasterVolumeLevelScalar() * 100))
                out(f"OK GET {p}")
            except Exception as ex: out("ERR GET " + str(ex))
        elif line.lower().startswith("set:"):
            try:
                v = max(0, min(100, int(line.split(":",1)[1].strip())))
                vol.SetMasterVolumeLevelScalar(v / 100.0, None)
                out(f"OK SET {v}")
            except Exception as ex: out("ERR SET " + str(ex))
        elif line.lower() == "quit":
            out("OK QUIT"); break
        else:
            out("ERR UNKNOWN " + line)
    except Exception as ex:
        out("ERR LOOP " + str(ex)); break

try: CoUninitialize()
except: pass
'''

tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".py", prefix="child_mic_server_")
tmp.write(child_code.encode("utf-8"))
tmp.flush(); tmp.close()
child_path = tmp.name
print("Child script written to:", child_path)

target_mode = "mic"
if len(sys.argv) > 1:
    target_mode = sys.argv[1].lower()

proc = subprocess.Popen([sys.executable, "-u", child_path, target_mode],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, bufsize=1)

stdout_q = queue.Queue()
stderr_q = queue.Queue()

def _read_stream(stream, q):
    try:
        for line in stream:
            q.put(line.rstrip("\n"))
    except Exception: pass
    finally:
        try: stream.close()
        except: pass

t_o = threading.Thread(target=_read_stream, args=(proc.stdout, stdout_q), daemon=True)
t_e = threading.Thread(target=_read_stream, args=(proc.stderr, stderr_q), daemon=True)
t_o.start(); t_e.start()

start_wait = time.time(); ready = False
while time.time() - start_wait < 6.0:
    try:
        ln = stdout_q.get(timeout=0.2)
    except queue.Empty:
        continue
    print("CHILD:", ln)
    if ln.strip().upper().startswith("OK READY"):
        ready = True; break

if not ready:
    while not stderr_q.empty():
        print("CHILD STDERR:", stderr_q.get())
    raise SystemExit("Child mic server did not start correctly.")

class MicController:
    def __init__(self, proc, stdout_q):
        self.proc = proc; self.stdout_q = stdout_q; self.lock = threading.Lock()

    def _send(self, line, timeout=2.0):
        with self.lock:
            if self.proc.poll() is not None:
                raise RuntimeError("Child process has exited.")
            try:
                self.proc.stdin.write(line.strip() + "\n"); self.proc.stdin.flush()
            except Exception as e:
                raise RuntimeError("Failed to write to child: " + str(e))
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    resp = self.stdout_q.get(timeout=0.2)
                    if resp is not None: return resp
                except queue.Empty: continue
            raise TimeoutError("No reply from child")

    def get_volume(self):
        resp = self._send("get")
        if resp.upper().startswith("OK GET"):
            try: return int(resp.split()[-1])
            except: return 50
        return 50

    def set_volume(self, percent):
        try: self._send(f"set:{int(percent)}")
        except: pass

    def close(self):
        try: self._send("quit", timeout=1.0)
        except: pass
        try: self.proc.kill()
        except: pass
        try: self.proc.wait(timeout=1.0)
        except: pass

mc = MicController(proc, stdout_q)

def get_mic_volume_percent():
    try: return mc.get_volume()
    except: return 50

def set_mic_volume_percent(v):
    try: mc.set_volume(int(v))
    except: pass

def set_system_volume(vol_percent):
    set_mic_volume_percent(int(vol_percent))

def get_system_volume():
    return get_mic_volume_percent()

print("[MAIN] Setup complete. Launching GUI...")

# ── Optional keyboard hotkeys ────────────────────────────────────────
try:
    import keyboard
    STEP_PERCENT = 2
    LAST = {"inc": 0, "dec": 0}
    COOLDOWN = 0.12

    def allow_action(k):
        now = time.time()
        if now - LAST[k] >= COOLDOWN:
            LAST[k] = now; return True
        return False

    def hot_inc():
        if not allow_action("inc"): return
        new = min(100, get_mic_volume_percent() + STEP_PERCENT)
        set_mic_volume_percent(new); print("[+] Mic ->", new, "%")

    def hot_dec():
        if not allow_action("dec"): return
        new = max(0, get_mic_volume_percent() - STEP_PERCENT)
        set_mic_volume_percent(new); print("[-] Mic ->", new, "%")

    keyboard.add_hotkey("ctrl+alt+up", hot_inc)
    keyboard.add_hotkey("ctrl+alt+down", hot_dec)
    print("Hotkeys registered: Ctrl+Alt+Up / Ctrl+Alt+Down")
except Exception:
    keyboard = None

class CameraStream:
    def __init__(self, cam_index=0, width=640, height=480):
        self.cam_index = cam_index
        self.width = width
        self.height = height
        try:
            self.cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        except Exception:
            self.cap = cv2.VideoCapture(cam_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        
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
        try: self.thread.join(timeout=1.0)
        except: pass
        try: self.cap.release()
        except: pass

# ── Hand detection using mediapipe Tasks API ─────────────────────────
base_options = mp_tasks.BaseOptions(model_asset_path=MODEL_PATH)
landmarker_options = mp_vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=2,
    min_hand_detection_confidence=0.35,
    min_hand_presence_confidence=0.35,
    min_tracking_confidence=0.4
)
hand_landmarker = mp_vision.HandLandmarker.create_from_options(landmarker_options)


def is_left_index_only(lm_list):
    """
    Checks if ONLY the index finger is raised (extended) on the left hand,
    while all other fingers (middle, ring, pinky, thumb) are folded.
    """
    if not lm_list or len(lm_list) < 21:
        return False

    # 1. Index finger must be raised (tip y should be significantly above pip y)
    index_raised = lm_list[8].y < lm_list[6].y

    # 2. Middle, Ring, Pinky must be folded (tip y should be below pip y)
    middle_folded = lm_list[12].y >= lm_list[10].y
    ring_folded = lm_list[16].y >= lm_list[14].y
    pinky_folded = lm_list[20].y >= lm_list[18].y

    # 3. Thumb must be folded (tip of thumb close to the index base or folded)
    try:
        if lm_list[17].x > lm_list[2].x:
            thumb_raised = lm_list[4].x < lm_list[2].x
        else:
            thumb_raised = lm_list[4].x > lm_list[2].x
    except Exception:
        thumb_raised = False

    return index_raised and middle_folded and ring_folded and pinky_folded and not thumb_raised


def draw_hand_skeleton(frame, landmarks, color, label_text):
    """Draws hand skeleton connections and wrist labels with background box."""
    img_h, img_w = frame.shape[:2]
    connections = [
        (0,1),(1,2),(2,3),(3,4),
        (0,5),(5,6),(6,7),(7,8),
        (5,9),(9,10),(10,11),(11,12),
        (9,13),(13,14),(14,15),(15,16),
        (13,17),(17,18),(18,19),(19,20),(0,17)
    ]
    pts = [(int(p.x*img_w), int(p.y*img_h)) for p in landmarks]
    for a, b in connections:
        try: cv2.line(frame, pts[a], pts[b], color, 2)
        except: pass
    for pt in pts:
        try: cv2.circle(frame, pt, 4, (255, 255, 255), -1)
        except: pass
        
    if pts:
        wrist_x, wrist_y = pts[0]
        text_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
        text_y = max(20, wrist_y - 5)
        text_rect_y1 = max(0, wrist_y - 20)
        text_rect_y2 = max(25, wrist_y + 5)
        cv2.rectangle(frame, (wrist_x - 10, text_rect_y1), (wrist_x + text_size[0] + 10, text_rect_y2), (0, 0, 0), -1)
        cv2.putText(frame, label_text, (wrist_x - 5, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


# ── Tkinter App ───────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title("Infosys_GestureVolume: Gesture -> Mic Volume")
        root.geometry("1280x780")
        root.configure(bg="#111")
        root.resizable(True, False)
        root.rowconfigure(0, weight=0); root.rowconfigure(1, weight=1); root.rowconfigure(2, weight=0)
        root.columnconfigure(0, weight=1)

        # Header
        hdr = tk.Frame(root, bg="#181818", height=70)
        hdr.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        hdr.grid_propagate(False)
        tk.Label(hdr, text="Infosys_GestureVolume: Volume Control with Hand Gestures",
                 font=("Consolas", 18, "bold"), fg="#00ffcc", bg="#181818",
                 anchor="e", padx=20).pack(fill=tk.X, pady=(5, 0))
        tk.Label(hdr,
                 text="Project made by BATCH A | SNEHIL GHOSH, GAUTAM N CHIPKAR, AMRUTHA VARSHANI, AYUSH GORGE",
                 font=("Consolas", 12), fg="#cccccc", bg="#181818",
                 anchor="e", padx=20).pack(fill=tk.X, pady=(0, 5))

        # Main area
        main = tk.Frame(root, bg="#111")
        main.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        main.columnconfigure(0, weight=1); main.columnconfigure(1, weight=3)
        main.rowconfigure(0, weight=1)

        # Graph panel
        self.graph_panel = tk.Frame(main, bg="#181818")
        self.graph_panel.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        for i in range(3): self.graph_panel.rowconfigure(i, weight=1)

        # Video panel
        self.video_label = tk.Label(main, bg="#000")
        self.video_label.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

        # Status bar
        self.status_label = tk.Label(root, text="Starting camera...",
                                      bg="#111", fg="#00ff99",
                                      font=("Consolas", 13), anchor="w", padx=10)
        self.status_label.grid(row=2, column=0, sticky="ew", pady=(0, 4))

        # Graph 1 — Live distances
        self.fig1 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax1  = self.fig1.add_subplot(111)
        self.ax1.set_facecolor("#111"); self.ax1.tick_params(colors="white")
        self.ax1.set_title("LIVE PINCH DISTANCE", color="white")
        self.line_norm, = self.ax1.plot([], [], label="Norm", color="#00ffcc")
        self.line_pix,  = self.ax1.plot([], [], label="Pixel (scaled)", color="#ff00ff")
        self.ax1.legend(facecolor="#222", labelcolor="white")
        self.canvas1 = FigureCanvasTkAgg(self.fig1, master=self.graph_panel)
        self.canvas1.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Graph 2 — Aspect Ratio
        self.fig2 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax2  = self.fig2.add_subplot(111)
        self.ax2.set_facecolor("#111"); self.ax2.tick_params(colors="white")
        self.ax2.set_title("Aspect Ratio", color="white")
        self.line_aspect, = self.ax2.plot([], [], color="#ffcc00")
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=self.graph_panel)
        self.canvas2.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        # Graph 3 — placeholder
        self.fig3 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax3  = self.fig3.add_subplot(111)
        self.ax3.set_facecolor("#111")
        self.ax3.text(0.5, 0.5, "Future Graph", color="gray",
                      ha="center", va="center", fontsize=14)
        self.canvas3 = FigureCanvasTkAgg(self.fig3, master=self.graph_panel)
        self.canvas3.get_tk_widget().grid(row=2, column=0, sticky="nsew")

        # Camera
        self.cap = CameraStream(CAM_INDEX, CAM_WIDTH, CAM_HEIGHT)

        self.running       = True
        self.prev_pixel_dist = None
        self.prev_volume   = get_system_volume()
        self.last_volume_query_time = time.time()

        # Two-hand responsibilities states
        self.lock_state = False
        self.left_index_detected = False
        self.left_index_gesture_triggered = False
        self.left_index_consecutive_true = 0
        self.left_index_consecutive_false = 0
        self.hud_message = ""
        self.hud_message_time = 0.0
        self.target_mode = target_mode
        self.timestamps    = deque(maxlen=150)
        self.norm_dists    = deque(maxlen=150)
        self.pixel_dists   = deque(maxlen=150)
        self.aspect_ratios = deque(maxlen=150)
        self.start_time    = time.time()

        # Latency/plot throttling states
        self.last_plot_time = 0.0
        self.smooth_pinch = None
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.fps = 0.0

        root.bind("<Key>", self._on_keypress)
        root.protocol("WM_DELETE_WINDOW", self.stop_and_close)
        self._update_frame()

    def update_lock_state(self, left_landmarks):
        """Updates Left Hand debouncing and Volume control lock toggling state."""
        detected_this_frame = is_left_index_only(left_landmarks)
        
        # Debounce settings
        STABILITY_FRAMES = 5
        gesture_triggered = False
        
        if detected_this_frame:
            self.left_index_consecutive_true += 1
            self.left_index_consecutive_false = 0
            if self.left_index_consecutive_true >= STABILITY_FRAMES:
                if not self.left_index_detected:
                    self.left_index_detected = True
                    gesture_triggered = True
        else:
            self.left_index_consecutive_false += 1
            self.left_index_consecutive_true = 0
            if self.left_index_consecutive_false >= STABILITY_FRAMES:
                self.left_index_detected = False
                
        if gesture_triggered:
            self.lock_state = not self.lock_state
            self.left_index_gesture_triggered = True
            self.hud_message = "VOLUME LOCKED" if self.lock_state else "VOLUME UNLOCKED"
            self.hud_message_time = time.time()
            print(f"[LOCK STATE TOGGLE] Locked: {self.lock_state}")
        else:
            self.left_index_gesture_triggered = False

    def update_right_hand_volume(self, right_landmarks, img_w, img_h):
        """Calculates volume based on right-hand pinch if unlocked."""
        if not right_landmarks:
            return None, None, False, self.prev_volume

        lm_thumb = right_landmarks[4]
        lm_index = right_landmarks[8]
        lm_wrist = right_landmarks[0]
        lm_mcp = right_landmarks[9]

        # 1. Calculate hand reference size in 3D normalized coordinates (wrist to middle MCP)
        ref_dx = lm_wrist.x - lm_mcp.x
        ref_dy = lm_wrist.y - lm_mcp.y
        ref_dz = lm_wrist.z - lm_mcp.z
        hand_ref_size = math.sqrt(ref_dx**2 + ref_dy**2 + ref_dz**2)
        if hand_ref_size < 1e-4:
            hand_ref_size = 1e-4

        # 2. Calculate raw pinch distance in 3D normalized coordinates
        dx_n = lm_thumb.x - lm_index.x
        dy_n = lm_thumb.y - lm_index.y
        dz_n = lm_thumb.z - lm_index.z
        norm_dist = math.sqrt(dx_n**2 + dy_n**2 + dz_n**2)

        # 3. Calculate distance-independent normalized pinch distance
        normalized_pinch = norm_dist / hand_ref_size

        # 4. Apply Exponential Moving Average (EMA) smoothing to the normalized pinch
        ALPHA = 0.4
        if self.smooth_pinch is None:
            self.smooth_pinch = normalized_pinch
        else:
            self.smooth_pinch = ALPHA * normalized_pinch + (1 - ALPHA) * self.smooth_pinch

        # 5. Determine pinch state using adaptive threshold
        pinch = self.smooth_pinch <= 0.18

        # 6. Map the smooth pinch to volume
        new_volume = self.prev_volume
        target_vol = int(np.clip(np.interp(self.smooth_pinch, [MIN_NORM_PINCH, MAX_NORM_PINCH], [0, 100]), 0, 100))
        if not self.lock_state:
            if abs(target_vol - self.prev_volume) >= VOLUME_STEP_THRESHOLD:
                set_system_volume(target_vol)
                new_volume = target_vol

        # Calculate pixel distance for status overlay and graphs
        tx_px = int(round(lm_thumb.x * img_w))
        ty_px = int(round(lm_thumb.y * img_h))
        ix_px = int(round(lm_index.x * img_w))
        iy_px = int(round(lm_index.y * img_h))
        pixel_dist = math.hypot(tx_px - ix_px, ty_px - iy_px)

        return norm_dist, pixel_dist, pinch, new_volume

    def process_frame(self, frame):
        """Orchestrates separation of hands, Lock/Unlock and Volume controls, and custom HUD overlays."""
        img_h, img_w = frame.shape[:2]
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        result = None
        t_start = time.time()
        try:
            result = hand_landmarker.detect(mp_image)
        except Exception:
            pass
        inference_time_ms = (time.time() - t_start) * 1000

        left_landmarks = None
        right_landmarks = None
        left_detected = False
        right_detected = False

        if result and result.hand_landmarks:
            for i, landmarks in enumerate(result.hand_landmarks):
                if i < len(result.handedness) and result.handedness[i]:
                    hand_info = result.handedness[i][0]
                    name = (hand_info.category_name or hand_info.display_name or "").strip()
                    if name.lower() == 'left':
                        right_landmarks = landmarks
                        right_detected = True
                    elif name.lower() == 'right':
                        left_landmarks = landmarks
                        left_detected = True

        # Update left hand lock state
        self.update_lock_state(left_landmarks)

        # Update right hand volume
        norm_dist, pixel_dist, pinch, self.prev_volume = self.update_right_hand_volume(right_landmarks, img_w, img_h)

        # Draw skeletons with custom BGR colors and labels
        if left_detected and left_landmarks:
            # Color: Cyan (255, 255, 0)
            draw_hand_skeleton(frame, left_landmarks, (255, 255, 0), "Left: Lock Toggle")
            if is_left_index_only(left_landmarks):
                ix_px = int(round(left_landmarks[8].x * img_w))
                iy_px = int(round(left_landmarks[8].y * img_h))
                cv2.circle(frame, (ix_px, iy_px), 12, (0, 255, 0), 2) # Toggle gesture highlight

        if right_detected and right_landmarks:
            # Color: Magenta/Pink (255, 0, 255)
            role_text = f"Right: Volume Control ({'LOCKED' if self.lock_state else 'ACTIVE'})"
            draw_hand_skeleton(frame, right_landmarks, (255, 0, 255), role_text)

            # Draw right-hand highlights
            tx_px = int(round(right_landmarks[4].x * img_w))
            ty_px = int(round(right_landmarks[4].y * img_h))
            ix_px = int(round(right_landmarks[8].x * img_w))
            iy_px = int(round(right_landmarks[8].y * img_h))

            cv2.circle(frame, (tx_px, ty_px), 10, (0, 0, 255), -1)
            cv2.circle(frame, (ix_px, iy_px), 10, (255, 0, 0), -1)
            cv2.line(frame, (tx_px, ty_px), (ix_px, iy_px), (0, 255, 0), 3)

            mid_x = (tx_px + ix_px) // 2
            mid_y = (ty_px + iy_px) // 2
            cv2.circle(frame, (mid_x, mid_y), 10, (0, 255, 255), -1)

            cv2.putText(frame, f"Volume: {self.prev_volume}%", (mid_x - 80, mid_y - 60),
                        cv2.FONT_HERSHEY_DUPLEX, 1.0, (255, 255, 0), 2, cv2.LINE_AA)

        # Aspect ratio of Right hand
        aspect_ratio = None
        if right_detected and right_landmarks:
            xs = [p.x for p in right_landmarks]
            ys = [p.y for p in right_landmarks]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            if height > 0:
                aspect_ratio = width / height

        # 4. HUD overlay
        target_label = "Speaker" if self.target_mode == "speaker" else "Mic"
        lines = [
            f"Left Hand:  {'DETECTED' if left_detected else 'NOT DETECTED'}",
            f"Right Hand: {'DETECTED' if right_detected else 'NOT DETECTED'}",
            f"Volume ({target_label}): {self.prev_volume}%",
            f"Control:    {'LOCKED' if self.lock_state else 'ACTIVE'}"
        ]
        for i, txt in enumerate(lines):
            color = (255, 255, 255)
            if "NOT DETECTED" in txt or "LOCKED" in txt:
                color = (0, 0, 255)
            elif "DETECTED" in txt or "ACTIVE" in txt:
                color = (0, 255, 0)
            elif "Volume" in txt:
                color = (255, 255, 0)

            cv2.putText(frame, txt, (10, 35 + i*35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

        # 5. Brief banner for state toggle
        if self.hud_message and (time.time() - self.hud_message_time < 2.0):
            text_size = cv2.getTextSize(self.hud_message, cv2.FONT_HERSHEY_DUPLEX, 1.2, 3)[0]
            text_x = (img_w - text_size[0]) // 2
            text_y = 120
            
            rect_color = (0, 0, 180) if "LOCKED" in self.hud_message else (0, 180, 0)
            cv2.rectangle(frame, (text_x - 20, text_y - 40), (text_x + text_size[0] + 20, text_y + 15), rect_color, -1)
            cv2.rectangle(frame, (text_x - 20, text_y - 40), (text_x + text_size[0] + 20, text_y + 15), (255, 255, 255), 2)
            
            cv2.putText(frame, self.hud_message, (text_x, text_y),
                        cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 3, cv2.LINE_AA)

        # 6. Debug overlay monitor
        if DEBUG_MODE:
            debug_lines = [
                f"DEBUG MONITOR",
                f"FPS: {self.fps:.1f}",
                f"MP Latency: {inference_time_ms:.1f}ms",
                f"Res: {img_w}x{img_h}",
                f"L-Hand: {'ON' if left_detected else 'OFF'}",
                f"R-Hand: {'ON' if right_detected else 'OFF'}",
                f"Norm Pinch: {self.smooth_pinch:.3f}" if self.smooth_pinch is not None else "Norm Pinch: N/A",
                f"Raw Pinch: {pixel_dist:.1f}px" if pixel_dist is not None else "Raw Pinch: N/A",
                f"Volume: {self.prev_volume}%",
                f"Lock: {self.lock_state}"
            ]
            for idx, txt in enumerate(debug_lines):
                cv2.putText(frame, txt, (img_w - 220, img_h - 10 - (len(debug_lines) - 1 - idx) * 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)

        return norm_dist, pixel_dist, pinch, aspect_ratio

    def _on_keypress(self, event):
        if hasattr(event, "char") and event.char and event.char.lower() == "q":
            self.stop_and_close()

    def _update_frame(self):
        if not self.running: return

        ret, frame = self.cap.read()
        if not ret or frame is None:
            self.root.after(10, self._update_frame)
            return

        # Calculate actual FPS
        now_time = time.time()
        self.frame_count += 1
        if now_time - self.last_fps_time >= 1.0:
            self.fps = self.frame_count / (now_time - self.last_fps_time)
            self.frame_count = 0
            self.last_fps_time = now_time

        # Periodically query audio device (every 2.0 seconds) to avoid blocking pipe reads on every frame
        if now_time - self.last_volume_query_time > 2.0:
            try:
                self.prev_volume = get_system_volume()
            except:
                pass
            self.last_volume_query_time = now_time

        frame = cv2.flip(frame, 1)
        norm_dist, pixel_dist, pinch, aspect_ratio = self.process_frame(frame)
        self.prev_pixel_dist = pixel_dist

        # Show video
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        lbl_w = self.video_label.winfo_width() or 960
        lbl_h = self.video_label.winfo_height() or 720
        try:  resample = Image.Resampling.LANCZOS
        except: resample = Image.LANCZOS
        pil_img = pil_img.resize((lbl_w, lbl_h), resample)
        imgtk = ImageTk.PhotoImage(pil_img)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

        # Update graph data
        elapsed = time.time() - self.start_time
        self.timestamps.append(elapsed)
        self.norm_dists.append(norm_dist if norm_dist is not None else float("nan"))
        self.pixel_dists.append(pixel_dist if pixel_dist is not None else float("nan"))
        self.aspect_ratios.append(aspect_ratio if aspect_ratio is not None else float("nan"))

        # Throttle Matplotlib plotting updates to 10 FPS (every 100ms) to reduce rendering lag
        if now_time - self.last_plot_time >= 0.1:
            self.last_plot_time = now_time
            t_arr    = np.array(self.timestamps)
            norm_arr = np.array(self.norm_dists)
            pix_arr  = np.array(self.pixel_dists)
            asp_arr  = np.array(self.aspect_ratios)

            # Graph 1
            try:
                with np.errstate(all="ignore"):
                    factor = 1.0
                    if np.nanmax(pix_arr) > 0 and np.nanmax(norm_arr) > 0:
                        factor = (np.nanmax(norm_arr)+1e-6) / (np.nanmax(pix_arr)+1e-6)
                scaled_pix = pix_arr * factor
                self.line_norm.set_data(t_arr, norm_arr)
                self.line_pix.set_data(t_arr, scaled_pix)
                self.ax1.set_xlim(max(0, elapsed-15), elapsed+0.1)
                y_all = np.concatenate([np.nan_to_num(norm_arr), np.nan_to_num(scaled_pix)])
                if np.any(np.isfinite(y_all)):
                    self.ax1.set_ylim(np.nanmin(y_all)-0.1, np.nanmax(y_all)+0.1)
                self.canvas1.draw_idle()
            except Exception: pass

            # Graph 2
            try:
                self.line_aspect.set_data(t_arr, asp_arr)
                self.ax2.set_xlim(max(0, elapsed-15), elapsed+0.1)
                fin = asp_arr[np.isfinite(asp_arr)]
                if len(fin) > 0:
                    self.ax2.set_ylim(fin.min()-0.1, fin.max()+0.1)
                self.canvas2.draw_idle()
            except Exception: pass

        # Status bar
        now      = time.strftime("%H:%M:%S")
        ns  = f"{norm_dist:.4f}"  if norm_dist   is not None else "0.0000"
        ps  = f"{pixel_dist:.1f}" if pixel_dist  is not None else "0.0"
        asp = f"{aspect_ratio:.2f}" if aspect_ratio is not None else "N/A"
        self.status_label.config(
            text=f"[{now}] | Target={self.target_mode.upper()} | Norm={ns} | Pixel={ps}px | Aspect={asp} | Volume={self.prev_volume}%"
        )
        self.root.after(10, self._update_frame)

    def stop_and_close(self):
        if not getattr(self, "running", False):
            try: self.root.destroy()
            except: pass
            return
        self.running = False
        try:
            self.cap.release()
        except: pass
        try: hand_landmarker.close()
        except: pass
        if keyboard:
            try: keyboard.unhook_all_hotkeys()
            except: pass
        try: mc.close()
        except: pass
        try: os.unlink(child_path)
        except: pass
        try: self.root.destroy()
        except: pass


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    try:
        root.mainloop()
    finally:
        try: mc.close()
        except: pass
        if keyboard:
            try: keyboard.unhook_all_hotkeys()
            except: pass
        print("Exited GUI and cleaned up.")
