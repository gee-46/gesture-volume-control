# Infosys GestureVolume: Finger -> Mic Volume
# Compatible with mediapipe >= 0.10.x (Tasks API)
# Windows only. Requires: pip install pycaw comtypes mediapipe opencv-python Pillow matplotlib

import sys, os, time, tempfile, threading, queue, subprocess, platform, traceback, math
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.components.containers import landmark as mp_landmark
import matplotlib
matplotlib.use('TkAgg')
import tkinter as tk
from PIL import Image, ImageTk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from collections import deque, Counter
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)

if platform.system() != "Windows":
    raise SystemExit("This script runs only on Windows.")

# --- Window constants ---
WIN_W = 1280
WIN_H = 720

# --- Config ---
CAM_INDEX            = 0
CAM_WIDTH            = 640
CAM_HEIGHT           = 480
DEBUG_MODE           = False

# --- MediaPipe HandLandmarker model path ---
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")
if not os.path.exists(MODEL_PATH):
    raise SystemExit(f"Model file not found: {MODEL_PATH}\nDownload it from: https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")

# --- Child process code for COM/pycaw mic control ---
child_code = r'''
import sys, traceback
def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)
try:
    from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
    from comtypes.client import CreateObject
    from comtypes import GUID
    from ctypes import POINTER, cast
    from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
except Exception as ex:
    print("ERR IMPORT", ex); eprint("IMPORT TRACEBACK:"); traceback.print_exc(); sys.exit(2)

def out(s):
    print(s); sys.stdout.flush()

try:
    CoInitialize()
except Exception as ex:
    out("ERR COINIT " + str(ex)); traceback.print_exc(); sys.exit(3)

def _create_enum():
    try:
        clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        return CreateObject(clsid, interface=IMMDeviceEnumerator)
    except Exception:
        return CreateObject("MMDeviceEnumerator.MMDeviceEnumerator", interface=IMMDeviceEnumerator)

def _get_vol_iface():
    enumerator = _create_enum()
    import sys
    dataflow = 0
    if len(sys.argv) > 1 and sys.argv[1].lower() == "mic":
        dataflow = 1
    device = enumerator.GetDefaultAudioEndpoint(dataflow, 0)
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    from ctypes import cast, POINTER
    return cast(iface, POINTER(IAudioEndpointVolume))

try:
    vol = _get_vol_iface()
except Exception as ex:
    out("ERR GET_IFACE " + str(ex)); traceback.print_exc()
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
            except Exception as ex:
                out("ERR GET " + str(ex))
        elif line.lower().startswith("set:"):
            try:
                v = max(0, min(100, int(line.split(":",1)[1].strip())))
                vol.SetMasterVolumeLevelScalar(v / 100.0, None)
                out(f"OK SET {v}")
            except Exception as ex:
                out("ERR SET " + str(ex))
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

target_mode = "speaker"
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
    except Exception:
        pass
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
                raise RuntimeError("Failed to write to child stdin: " + str(e))
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    resp = self.stdout_q.get(timeout=0.2)
                    if resp is not None: return resp
                except queue.Empty:
                    continue
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

def get_mic_is_muted():
    return False

def fingers_to_percent(n):
    return {0: 0, 1: 20, 2: 40, 3: 60, 4: 80, 5: 100}.get(n, 0)

print("[MAIN] Setup complete. Launching GUI...")

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

# ─────────────────────────────────────────────
#  App class — uses mediapipe Tasks HandLandmarker
# ─────────────────────────────────────────────
class App:
    def __init__(self, stable_frames=6, cam_index=0):
        self.stable_frames = stable_frames
        self.cam_index = cam_index

        # 1. Camera
        self.cap = CameraStream(CAM_INDEX, CAM_WIDTH, CAM_HEIGHT)

        # 2. MediaPipe HandLandmarker (Tasks API)
        base_options = mp_tasks.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=2,
            min_hand_detection_confidence=0.35,
            min_hand_presence_confidence=0.35,
            min_tracking_confidence=0.4
        )
        self.hand_landmarker = mp_vision.HandLandmarker.create_from_options(options)

        # 3. Drawing utilities (still available in 0.10 as a standalone)
        try:
            from mediapipe.python.solutions import drawing_utils, drawing_styles, hands as hands_sol
            self._drawing_utils = drawing_utils
            self._drawing_styles = drawing_styles
            self._hands_connections = hands_sol.HAND_CONNECTIONS
        except Exception:
            self._drawing_utils = None

        # 4. Logic & State
        self.history = deque(maxlen=self.stable_frames)
        self.current_applied = get_mic_volume_percent()
        self.last_volume_query_time = time.time()
        self.last_observed = 0
        self.running = True
        self.start_time = time.time()

        # Two-hand responsibilities states
        self.lock_state = False
        self.left_index_detected = False
        self.left_index_gesture_triggered = False
        self.left_index_consecutive_true = 0
        self.left_index_consecutive_false = 0
        self.hud_message = ""
        self.hud_message_time = 0.0
        self.target_mode = target_mode

        # Latency/plot throttling states
        self.last_plot_time = 0.0
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.fps = 0.0

        # ── UI SETUP ──────────────────────────────────
        self.root = tk.Tk()
        self.root.title("Infosys_GestureVolume: Finger -> Mic Volume")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.configure(bg="#050505")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=0)
        self.root.columnconfigure(0, weight=1)

        # HEADER
        self.header_frame = tk.Frame(self.root, bg="#111", height=70)
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 5))
        self.header_frame.grid_propagate(False)
        tk.Label(self.header_frame, text="Infosys_GestureVolume: Volume Control with Hand Gestures",
                 font=("Consolas", 18, "bold"), fg="#00ffcc", bg="#111", anchor="w", padx=20
                 ).pack(fill=tk.X, pady=(5, 0))
        tk.Label(self.header_frame,
                 text="Project by BATCH A | SNEHIL GHOSH, GAUTAM N CHIPKAR, AMRUTHA VARSHANI, AYUSH GORGE",
                 font=("Consolas", 10), fg="#cccccc", bg="#111", anchor="w", padx=20
                 ).pack(fill=tk.X, pady=(0, 5))

        # MAIN CONTENT
        self.main_frame = tk.Frame(self.root, bg="#050505")
        self.main_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=3)
        self.main_frame.rowconfigure(0, weight=1)

        # LEFT PANEL (HUD)
        self.hud_panel = tk.Frame(self.main_frame, bg="#050505")
        self.hud_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.hud_panel.rowconfigure(0, weight=1)
        self.hud_panel.rowconfigure(1, weight=1)

        # GRAPH 1: ARC REACTOR
        self.fig_arc = Figure(figsize=(3, 3), dpi=100, facecolor='#050505')
        self.ax_arc = self.fig_arc.add_subplot(111, projection='polar')
        self.ax_arc.set_facecolor('#050505')
        self.ax_arc.grid(False)
        self.ax_arc.set_xticklabels([])
        self.ax_arc.set_yticklabels([])
        self.ax_arc.spines['polar'].set_visible(False)
        self.ax_arc.bar([0], [2.4], width=2*math.pi, color='#151515', bottom=0.0)
        self.arc_bar = self.ax_arc.bar([0], [2.4], width=0, color='#00ffff', bottom=0.0)[0]
        self.ax_arc.set_ylim(0, 2.5)
        self.text_vol = self.ax_arc.text(0, 0, "0%", ha='center', va='center',
                                          color='white', fontsize=18, fontweight='bold', zorder=10)
        self.ax_arc.text(0, -1.5, "VOLUME", ha='center', va='center',
                         color='black', fontsize=9, fontweight='bold', zorder=10)
        self.canvas_arc = FigureCanvasTkAgg(self.fig_arc, master=self.hud_panel)
        self.canvas_arc.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # GRAPH 2: Z-AXIS PROXIMITY
        self.fig_prox = Figure(figsize=(3, 2), dpi=100, facecolor='#050505')
        self.ax_prox = self.fig_prox.add_subplot(111)
        self.ax_prox.set_facecolor('#0f0f0f')
        self.ax_prox.set_title("Z-AXIS PROXIMITY SENSOR", color='#00ffcc', fontsize=9, pad=10)
        self.ax_prox.set_ylabel("Depth Intensity", color='#888', fontsize=8)
        self.ax_prox.tick_params(colors='#888', labelsize=7)
        self.ax_prox.set_xticks([])
        self.ax_prox.grid(True, axis='y', color="#333", linestyle="--", linewidth=0.5)
        self.bar_prox = self.ax_prox.bar([0], [0], width=0.4, color='#00ff00', alpha=0.8)[0]
        self.ax_prox.set_ylim(0, 1.0)
        self.ax_prox.set_xlim(-0.5, 0.5)
        self.ax_prox.axhline(0.8, color='#ff3333', linestyle=':', linewidth=1)
        self.ax_prox.text(0.3, 0.82, "CRITICAL", color='#ff3333', fontsize=6)
        self.canvas_prox = FigureCanvasTkAgg(self.fig_prox, master=self.hud_panel)
        self.canvas_prox.get_tk_widget().grid(row=1, column=0, sticky="nsew", pady=10)

        # RIGHT: VIDEO
        self.video_label = tk.Label(self.main_frame, bg="black")
        self.video_label.grid(row=0, column=1, sticky="nsew")

        # FOOTER
        self.status_label = tk.Label(self.root, text="System Ready", bg="#111", fg="#00ff99",
                                      font=("Consolas", 12), anchor="w", padx=10, pady=5)
        self.status_label.grid(row=2, column=0, sticky="ew")

        self.root.bind("<Key>", self._on_keypress)
        self.root.after(10, self._update_frame)

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

    def _on_keypress(self, event):
        try:
            if hasattr(event, "char") and event.char and event.char.lower() == "q":
                self._on_close()
        except: pass

    def _detect_fingers(self, lm_list):
        """Count raised fingers from 21 landmarks (NormalizedLandmark list)."""
        if not lm_list or len(lm_list) < 21:
            return 0
        fingers_found = 0
        tips_pips = [(8, 6), (12, 10), (16, 14), (20, 18)]
        for tip, pip in tips_pips:
            try:
                if lm_list[tip].y < lm_list[pip].y:
                    fingers_found += 1
            except: pass
        try:
            # Thumb: compare x coords
            if lm_list[17].x > lm_list[2].x:
                if lm_list[4].x < lm_list[2].x: fingers_found += 1
            else:
                if lm_list[4].x > lm_list[2].x: fingers_found += 1
        except: pass
        return max(0, min(5, fingers_found))

    def _draw_landmarks_manual(self, frame, lm_list, img_w, img_h):
        """Draw hand skeleton manually without old drawing_utils."""
        connections = [
            (0,1),(1,2),(2,3),(3,4),
            (0,5),(5,6),(6,7),(7,8),
            (5,9),(9,10),(10,11),(11,12),
            (9,13),(13,14),(14,15),(15,16),
            (13,17),(17,18),(18,19),(19,20),
            (0,17)
        ]
        pts = [(int(lm.x * img_w), int(lm.y * img_h)) for lm in lm_list]
        for a, b in connections:
            try: cv2.line(frame, pts[a], pts[b], (255, 0, 255), 2)
            except: pass
        for pt in pts:
            try: cv2.circle(frame, pt, 3, (0, 255, 255), -1)
            except: pass

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
                self.current_applied = get_mic_volume_percent()
            except:
                pass
            self.last_volume_query_time = now_time

        frame = cv2.flip(frame, 1)
        img_h, img_w = frame.shape[:2]
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Run hand detection
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        result = None
        t_start = time.time()
        try:
            result = self.hand_landmarker.detect(mp_image)
        except: pass
        inference_time_ms = (time.time() - t_start) * 1000

        # Collect detected hands and sort by X position (left vs right on screen)
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
            if is_left_index_only(lm) and not self.lock_state:
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

        # Update left hand lock state
        self.update_lock_state(left_landmarks)

        fingers_found = 0
        wrist_z_estimate = 0.0

        # Draw skeletons with custom BGR colors and labels
        if left_detected and left_landmarks:
            draw_hand_skeleton(frame, left_landmarks, (255, 255, 0), "Left: Lock Toggle")
            if is_left_index_only(left_landmarks):
                ix_px = int(round(left_landmarks[8].x * img_w))
                iy_px = int(round(left_landmarks[8].y * img_h))
                cv2.circle(frame, (ix_px, iy_px), 12, (0, 255, 0), 2)

        if right_detected and right_landmarks:
            role_text = f"Right: Finger Counting ({'LOCKED' if self.lock_state else 'ACTIVE'})"
            draw_hand_skeleton(frame, right_landmarks, (255, 0, 255), role_text)

            # Count fingers from right hand
            fingers_found = self._detect_fingers(right_landmarks)

            # Z-proximity: wrist(0) to middle MCP(9) on right hand
            try:
                x0, y0 = right_landmarks[0].x, right_landmarks[0].y
                x9, y9 = right_landmarks[9].x, right_landmarks[9].y
                dist = math.sqrt((x9-x0)**2 + (y9-y0)**2)
                norm_prox = (dist - 0.1) * 3.5
                wrist_z_estimate = max(0.0, min(1.0, norm_prox))
            except:
                wrist_z_estimate = 0.0

        # Logic
        self.history.append(fingers_found)
        chosen = self.history[-1]
        try:
            if len(self.history) == self.history.maxlen:
                counts = Counter(self.history)
                most_common = counts.most_common()
                if most_common:
                    chosen = max([v for v, c in most_common if c == most_common[0][1]])
        except: pass

        target_pct = fingers_to_percent(chosen)
        if len(self.history) == self.history.maxlen and target_pct != self.current_applied:
            if not self.lock_state:
                try:
                    set_mic_volume_percent(target_pct)
                    self.current_applied = get_mic_volume_percent()
                    print(f"[APPLY] Fingers {chosen} -> {target_pct}%")
                except: pass
            else:
                pass

        self.last_observed = chosen
        muted = get_mic_is_muted()

        # HUD: Arc reactor (Throttled update rate along with proximity)
        if now_time - self.last_plot_time >= 0.1:
            self.last_plot_time = now_time
            try:
                vol_rad = (self.current_applied / 100.0) * (2 * math.pi)
                self.arc_bar.set_width(vol_rad)
                col = '#00ffff' if self.current_applied < 50 else ('#ff00ff' if self.current_applied < 80 else '#ff3333')
                self.arc_bar.set_color(col)
                self.text_vol.set_text(f"{int(self.current_applied)}%")
                self.canvas_arc.draw_idle()
            except: pass

            # HUD: Proximity
            try:
                self.bar_prox.set_height(wrist_z_estimate)
                prox_col = '#00ff00' if wrist_z_estimate < 0.5 else ('#ffcc00' if wrist_z_estimate < 0.8 else '#ff0000')
                self.bar_prox.set_color(prox_col)
                self.canvas_prox.draw_idle()
            except: pass

        # Video overlay
        display_frame = self._overlay_text(frame.copy(), left_detected, right_detected, self.last_observed, self.current_applied, muted)
        
        # Debug overlay monitor
        if DEBUG_MODE:
            debug_lines = [
                f"DEBUG MONITOR",
                f"FPS: {self.fps:.1f}",
                f"MP Latency: {inference_time_ms:.1f}ms",
                f"Res: {img_w}x{img_h}",
                f"L-Hand: {'ON' if left_detected else 'OFF'}",
                f"R-Hand: {'ON' if right_detected else 'OFF'}",
                f"Fingers: {self.last_observed}",
                f"Volume: {self.current_applied}%",
                f"Lock: {self.lock_state}"
            ]
            for idx, txt in enumerate(debug_lines):
                cv2.putText(display_frame, txt, (img_w - 220, img_h - 10 - (len(debug_lines) - 1 - idx) * 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)

        img_rgb2 = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(img_rgb2)
        try:
            lbl_w = self.video_label.winfo_width() or 1
            lbl_h = self.video_label.winfo_height() or 1
            pil = pil.resize((lbl_w, lbl_h), Image.LANCZOS)
        except: pass
        imgtk = ImageTk.PhotoImage(image=pil)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

        # Status
        now = time.strftime("%H:%M:%S")
        self.status_label.config(
            text=f"[{now}] | SYS: ONLINE | Target: {self.target_mode.upper()} | Fingers: {self.last_observed} | Vol: {self.current_applied}% | Mute: {muted} | Z-Depth: {wrist_z_estimate:.2f}"
        )
        self.root.after(10, self._update_frame)

    def _overlay_text(self, frame, left_detected, right_detected, fingers, volume, muted):
        img_h, img_w = frame.shape[:2]
        
        # 1. Overlay left status text
        target_label = "Speaker" if self.target_mode == "speaker" else "Mic"
        lines = [
            f"Left Hand:  {'DETECTED' if left_detected else 'NOT DETECTED'}",
            f"Right Hand: {'DETECTED' if right_detected else 'NOT DETECTED'}",
            f"Fingers:    {fingers}",
            f"Volume ({target_label}): {volume}%",
            f"Control:    {'LOCKED' if self.lock_state else 'ACTIVE'}"
        ]
        for i, txt in enumerate(lines):
            color = (255, 255, 255)
            if "NOT DETECTED" in txt or "LOCKED" in txt:
                color = (0, 0, 255)
            elif "DETECTED" in txt or "ACTIVE" in txt:
                color = (0, 255, 0)
            elif "Volume" in txt or "Fingers" in txt:
                color = (255, 255, 0)

            cv2.putText(frame, txt, (10, 35 + i*35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

        # 2. Brief banner for state toggle
        if self.hud_message and (time.time() - self.hud_message_time < 2.0):
            text_size = cv2.getTextSize(self.hud_message, cv2.FONT_HERSHEY_DUPLEX, 1.2, 3)[0]
            text_x = (img_w - text_size[0]) // 2
            text_y = 120
            
            rect_color = (0, 0, 180) if "LOCKED" in self.hud_message else (0, 180, 0)
            cv2.rectangle(frame, (text_x - 20, text_y - 40), (text_x + text_size[0] + 20, text_y + 15), rect_color, -1)
            cv2.rectangle(frame, (text_x - 20, text_y - 40), (text_x + text_size[0] + 20, text_y + 15), (255, 255, 255), 2)
            
            cv2.putText(frame, self.hud_message, (text_x, text_y),
                        cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 3, cv2.LINE_AA)
            
        return frame

    def _on_close(self):
        self.running = False
        try: self.cap.release()
        except: pass
        try: self.hand_landmarker.close()
        except: pass
        try: mc.close()
        except: pass
        try: os.unlink(child_path)
        except: pass
        try: self.root.destroy()
        except: pass
        sys.exit(0)

    def run(self):
        try: self.root.mainloop()
        finally:
            try: mc.close()
            except: pass

if __name__ == "__main__":
    app = App()
    app.run()